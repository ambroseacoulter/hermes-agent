#!/usr/bin/env python3
"""
SQLite State Store for Hermes Agent.

Provides persistent session storage with FTS5 full-text search, replacing
the per-session JSONL file approach. Stores session metadata, full message
history, and model configuration for CLI and gateway sessions.

Key design decisions:
- WAL mode for concurrent readers + one writer (gateway multi-platform)
- FTS5 virtual table for fast text search across all session messages
- Compression-triggered session splitting via parent_session_id chains
- Batch runner and RL trajectories are NOT stored here (separate systems)
- Session source tagging ('cli', 'telegram', 'discord', etc.) for filtering
"""

import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_DB_PATH = get_hermes_home() / "state.db"

SCHEMA_VERSION = 7

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    billing_provider TEXT,
    billing_base_url TEXT,
    billing_mode TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    pricing_version TEXT,
    title TEXT,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_details TEXT,
    codex_reasoning_items TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);

CREATE TABLE IF NOT EXISTS autonomy_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    current_revision INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0,
    last_supervisor_run_at REAL,
    last_home_delivery_at REAL,
    last_social_nudge_at REAL
);

CREATE TABLE IF NOT EXISTS autonomy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    session_key TEXT,
    session_id TEXT,
    status TEXT NOT NULL,
    summary TEXT,
    payload TEXT,
    created_at REAL NOT NULL,
    finished_at REAL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_autonomy_runs_created ON autonomy_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS autonomy_watch_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    importance TEXT NOT NULL DEFAULT 'normal',
    source_session_key TEXT,
    source_message_ref TEXT,
    inference_mode TEXT NOT NULL DEFAULT 'implied',
    due_at REAL,
    status TEXT NOT NULL DEFAULT 'active',
    next_check_at REAL,
    last_checked_at REAL,
    last_changed_at REAL NOT NULL,
    metadata TEXT,
    revision INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_autonomy_watch_status ON autonomy_watch_items(status, next_check_at, last_changed_at DESC);

CREATE TABLE IF NOT EXISTS autonomy_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    watch_item_id INTEGER,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    details TEXT,
    importance TEXT NOT NULL DEFAULT 'normal',
    category TEXT NOT NULL DEFAULT 'utility',
    message_preview TEXT,
    created_at REAL NOT NULL,
    revision INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES autonomy_runs(id),
    FOREIGN KEY (watch_item_id) REFERENCES autonomy_watch_items(id)
);

CREATE INDEX IF NOT EXISTS idx_autonomy_findings_created ON autonomy_findings(created_at DESC);

CREATE TABLE IF NOT EXISTS autonomy_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    watch_item_id INTEGER,
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    payload TEXT,
    target TEXT,
    execution_requirements TEXT,
    importance TEXT NOT NULL DEFAULT 'normal',
    category TEXT NOT NULL DEFAULT 'utility',
    approval_required INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    message_preview TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    revision INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES autonomy_runs(id),
    FOREIGN KEY (watch_item_id) REFERENCES autonomy_watch_items(id)
);

CREATE INDEX IF NOT EXISTS idx_autonomy_artifacts_status ON autonomy_artifacts(status, created_at DESC);

CREATE TABLE IF NOT EXISTS autonomy_inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    message_preview TEXT,
    importance TEXT NOT NULL DEFAULT 'normal',
    category TEXT NOT NULL DEFAULT 'utility',
    approval_required INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    seen_at REAL,
    last_delivered_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    revision INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_autonomy_inbox_source_unique
ON autonomy_inbox_items(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_autonomy_inbox_status ON autonomy_inbox_items(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_autonomy_inbox_revision ON autonomy_inbox_items(revision DESC);

CREATE TABLE IF NOT EXISTS autonomy_delivery_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inbox_item_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    message_text TEXT,
    target_platform TEXT,
    target_chat_id TEXT,
    target_thread_id TEXT,
    created_at REAL NOT NULL,
    sent_at REAL,
    error TEXT,
    FOREIGN KEY (inbox_item_id) REFERENCES autonomy_inbox_items(id)
);

CREATE INDEX IF NOT EXISTS idx_autonomy_delivery_attempts_inbox ON autonomy_delivery_attempts(inbox_item_id, created_at DESC);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


class SessionDB:
    """
    SQLite-backed session storage with FTS5 search.

    Thread-safe for the common gateway pattern (multiple reader threads,
    single writer via WAL mode). Each method opens its own cursor.
    """

    # ── Write-contention tuning ──
    # With multiple hermes processes (gateway + CLI sessions + worktree agents)
    # all sharing one state.db, WAL write-lock contention causes visible TUI
    # freezes.  SQLite's built-in busy handler uses a deterministic sleep
    # schedule that causes convoy effects under high concurrency.
    #
    # Instead, we keep the SQLite timeout short (1s) and handle retries at the
    # application level with random jitter, which naturally staggers competing
    # writers and avoids the convoy.
    _WRITE_MAX_RETRIES = 15
    _WRITE_RETRY_MIN_S = 0.020   # 20ms
    _WRITE_RETRY_MAX_S = 0.150   # 150ms
    # Attempt a PASSIVE WAL checkpoint every N successful writes.
    _CHECKPOINT_EVERY_N_WRITES = 50

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._write_count = 0
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            # Short timeout — application-level retry with random jitter
            # handles contention instead of sitting in SQLite's internal
            # busy handler for up to 30s.
            timeout=1.0,
            # Autocommit mode: Python's default isolation_level="" auto-starts
            # transactions on DML, which conflicts with our explicit
            # BEGIN IMMEDIATE.  None = we manage transactions ourselves.
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._init_schema()

    # ── Core write helper ──

    def _execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Execute a write transaction with BEGIN IMMEDIATE and jitter retry.

        *fn* receives the connection and should perform INSERT/UPDATE/DELETE
        statements.  The caller must NOT call ``commit()`` — that's handled
        here after *fn* returns.

        BEGIN IMMEDIATE acquires the WAL write lock at transaction start
        (not at commit time), so lock contention surfaces immediately.
        On ``database is locked``, we release the Python lock, sleep a
        random 20-150ms, and retry — breaking the convoy pattern that
        SQLite's built-in deterministic backoff creates.

        Returns whatever *fn* returns.
        """
        last_err: Optional[Exception] = None
        for attempt in range(self._WRITE_MAX_RETRIES):
            try:
                with self._lock:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(self._conn)
                        self._conn.commit()
                    except BaseException:
                        try:
                            self._conn.rollback()
                        except Exception:
                            pass
                        raise
                # Success — periodic best-effort checkpoint.
                self._write_count += 1
                if self._write_count % self._CHECKPOINT_EVERY_N_WRITES == 0:
                    self._try_wal_checkpoint()
                return result
            except sqlite3.OperationalError as exc:
                err_msg = str(exc).lower()
                if "locked" in err_msg or "busy" in err_msg:
                    last_err = exc
                    if attempt < self._WRITE_MAX_RETRIES - 1:
                        jitter = random.uniform(
                            self._WRITE_RETRY_MIN_S,
                            self._WRITE_RETRY_MAX_S,
                        )
                        time.sleep(jitter)
                        continue
                # Non-lock error or retries exhausted — propagate.
                raise
        # Retries exhausted (shouldn't normally reach here).
        raise last_err or sqlite3.OperationalError(
            "database is locked after max retries"
        )

    def _try_wal_checkpoint(self) -> None:
        """Best-effort PASSIVE WAL checkpoint.  Never blocks, never raises.

        Flushes committed WAL frames back into the main DB file for any
        frames that no other connection currently needs.  Keeps the WAL
        from growing unbounded when many processes hold persistent
        connections.
        """
        try:
            with self._lock:
                result = self._conn.execute(
                    "PRAGMA wal_checkpoint(PASSIVE)"
                ).fetchone()
                if result and result[1] > 0:
                    logger.debug(
                        "WAL checkpoint: %d/%d pages checkpointed",
                        result[2], result[1],
                    )
        except Exception:
            pass  # Best effort — never fatal.

    def close(self):
        """Close the database connection.

        Attempts a PASSIVE WAL checkpoint first so that exiting processes
        help keep the WAL file from growing unbounded.
        """
        with self._lock:
            if self._conn:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except Exception:
                    pass
                self._conn.close()
                self._conn = None

    def _init_schema(self):
        """Create tables and FTS if they don't exist, run migrations."""
        cursor = self._conn.cursor()

        cursor.executescript(SCHEMA_SQL)

        # Check schema version and run migrations
        cursor.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        if row is None:
            cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        else:
            current_version = row["version"] if isinstance(row, sqlite3.Row) else row[0]
            if current_version < 2:
                # v2: add finish_reason column to messages
                try:
                    cursor.execute("ALTER TABLE messages ADD COLUMN finish_reason TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 2")
            if current_version < 3:
                # v3: add title column to sessions
                try:
                    cursor.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 3")
            if current_version < 4:
                # v4: add unique index on title (NULLs allowed, only non-NULL must be unique)
                try:
                    cursor.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_title_unique "
                        "ON sessions(title) WHERE title IS NOT NULL"
                    )
                except sqlite3.OperationalError:
                    pass  # Index already exists
                cursor.execute("UPDATE schema_version SET version = 4")
            if current_version < 5:
                new_columns = [
                    ("cache_read_tokens", "INTEGER DEFAULT 0"),
                    ("cache_write_tokens", "INTEGER DEFAULT 0"),
                    ("reasoning_tokens", "INTEGER DEFAULT 0"),
                    ("billing_provider", "TEXT"),
                    ("billing_base_url", "TEXT"),
                    ("billing_mode", "TEXT"),
                    ("estimated_cost_usd", "REAL"),
                    ("actual_cost_usd", "REAL"),
                    ("cost_status", "TEXT"),
                    ("cost_source", "TEXT"),
                    ("pricing_version", "TEXT"),
                ]
                for name, column_type in new_columns:
                    try:
                        # name and column_type come from the hardcoded tuple above,
                        # not user input. Double-quote identifier escaping is applied
                        # as defense-in-depth; SQLite DDL cannot be parameterized.
                        safe_name = name.replace('"', '""')
                        cursor.execute(f'ALTER TABLE sessions ADD COLUMN "{safe_name}" {column_type}')
                    except sqlite3.OperationalError:
                        pass
                cursor.execute("UPDATE schema_version SET version = 5")
            if current_version < 6:
                # v6: add reasoning columns to messages table — preserves assistant
                # reasoning text and structured reasoning_details across gateway
                # session turns.  Without these, reasoning chains are lost on
                # session reload, breaking multi-turn reasoning continuity for
                # providers that replay reasoning (OpenRouter, OpenAI, Nous).
                for col_name, col_type in [
                    ("reasoning", "TEXT"),
                    ("reasoning_details", "TEXT"),
                    ("codex_reasoning_items", "TEXT"),
                ]:
                    try:
                        safe = col_name.replace('"', '""')
                        cursor.execute(
                            f'ALTER TABLE messages ADD COLUMN "{safe}" {col_type}'
                        )
                    except sqlite3.OperationalError:
                        pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 6")
            if current_version < 7:
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS autonomy_state (
                        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                        current_revision INTEGER NOT NULL DEFAULT 0,
                        paused INTEGER NOT NULL DEFAULT 0,
                        last_supervisor_run_at REAL,
                        last_home_delivery_at REAL,
                        last_social_nudge_at REAL
                    )"""
                )
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS autonomy_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_type TEXT NOT NULL,
                        session_key TEXT,
                        session_id TEXT,
                        status TEXT NOT NULL,
                        summary TEXT,
                        payload TEXT,
                        created_at REAL NOT NULL,
                        finished_at REAL,
                        error TEXT
                    )"""
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_autonomy_runs_created ON autonomy_runs(created_at DESC)")
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS autonomy_watch_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        normalized_key TEXT NOT NULL UNIQUE,
                        kind TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT,
                        importance TEXT NOT NULL DEFAULT 'normal',
                        source_session_key TEXT,
                        source_message_ref TEXT,
                        inference_mode TEXT NOT NULL DEFAULT 'implied',
                        due_at REAL,
                        status TEXT NOT NULL DEFAULT 'active',
                        next_check_at REAL,
                        last_checked_at REAL,
                        last_changed_at REAL NOT NULL,
                        metadata TEXT,
                        revision INTEGER NOT NULL
                    )"""
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_autonomy_watch_status ON autonomy_watch_items(status, next_check_at, last_changed_at DESC)")
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS autonomy_findings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id INTEGER,
                        watch_item_id INTEGER,
                        kind TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT,
                        details TEXT,
                        importance TEXT NOT NULL DEFAULT 'normal',
                        category TEXT NOT NULL DEFAULT 'utility',
                        message_preview TEXT,
                        created_at REAL NOT NULL,
                        revision INTEGER NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES autonomy_runs(id),
                        FOREIGN KEY (watch_item_id) REFERENCES autonomy_watch_items(id)
                    )"""
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_autonomy_findings_created ON autonomy_findings(created_at DESC)")
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS autonomy_artifacts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id INTEGER,
                        watch_item_id INTEGER,
                        artifact_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT,
                        payload TEXT,
                        target TEXT,
                        execution_requirements TEXT,
                        importance TEXT NOT NULL DEFAULT 'normal',
                        category TEXT NOT NULL DEFAULT 'utility',
                        approval_required INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'draft',
                        message_preview TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        revision INTEGER NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES autonomy_runs(id),
                        FOREIGN KEY (watch_item_id) REFERENCES autonomy_watch_items(id)
                    )"""
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_autonomy_artifacts_status ON autonomy_artifacts(status, created_at DESC)")
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS autonomy_inbox_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_type TEXT NOT NULL,
                        source_id INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        message_preview TEXT,
                        importance TEXT NOT NULL DEFAULT 'normal',
                        category TEXT NOT NULL DEFAULT 'utility',
                        approval_required INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'pending',
                        seen_at REAL,
                        last_delivered_at REAL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        revision INTEGER NOT NULL
                    )"""
                )
                cursor.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_autonomy_inbox_source_unique "
                    "ON autonomy_inbox_items(source_type, source_id)"
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_autonomy_inbox_status ON autonomy_inbox_items(status, created_at DESC)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_autonomy_inbox_revision ON autonomy_inbox_items(revision DESC)")
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS autonomy_delivery_attempts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        inbox_item_id INTEGER NOT NULL,
                        mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        message_text TEXT,
                        target_platform TEXT,
                        target_chat_id TEXT,
                        target_thread_id TEXT,
                        created_at REAL NOT NULL,
                        sent_at REAL,
                        error TEXT,
                        FOREIGN KEY (inbox_item_id) REFERENCES autonomy_inbox_items(id)
                    )"""
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_autonomy_delivery_attempts_inbox "
                    "ON autonomy_delivery_attempts(inbox_item_id, created_at DESC)"
                )
                cursor.execute("UPDATE schema_version SET version = 7")

        # Unique title index — always ensure it exists (safe to run after migrations
        # since the title column is guaranteed to exist at this point)
        try:
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_title_unique "
                "ON sessions(title) WHERE title IS NOT NULL"
            )
        except sqlite3.OperationalError:
            pass  # Index already exists

        # FTS5 setup (separate because CREATE VIRTUAL TABLE can't be in executescript with IF NOT EXISTS reliably)
        try:
            cursor.execute("SELECT * FROM messages_fts LIMIT 0")
        except sqlite3.OperationalError:
            cursor.executescript(FTS_SQL)

        cursor.execute(
            "INSERT OR IGNORE INTO autonomy_state (singleton_id, current_revision, paused) VALUES (1, 0, 0)"
        )

        self._repair_autonomy_schema(cursor)

        self._conn.commit()

    def _repair_autonomy_schema(self, cursor: sqlite3.Cursor) -> None:
        """Repair legacy autonomy tables that may exist with stale column sets.

        Earlier prototype branches created some of the same autonomy table names
        with different schemas. If a user keeps that SQLite file, a plain
        CREATE TABLE IF NOT EXISTS will not fix missing columns. We therefore
        run a lightweight column repair pass on every startup.
        """
        required_columns = {
            "autonomy_runs": {
                "run_type": "TEXT NOT NULL DEFAULT 'supervisor'",
                "payload": "TEXT",
            },
            "autonomy_watch_items": {
                "source_session_key": "TEXT",
                "source_message_ref": "TEXT",
                "inference_mode": "TEXT NOT NULL DEFAULT 'implied'",
                "due_at": "REAL",
                "next_check_at": "REAL",
                "last_checked_at": "REAL",
                "last_changed_at": "REAL NOT NULL DEFAULT 0",
                "metadata": "TEXT",
                "revision": "INTEGER NOT NULL DEFAULT 0",
            },
            "autonomy_findings": {
                "details": "TEXT",
                "message_preview": "TEXT",
                "revision": "INTEGER NOT NULL DEFAULT 0",
            },
            "autonomy_artifacts": {
                "payload": "TEXT",
                "target": "TEXT",
                "execution_requirements": "TEXT",
                "approval_required": "INTEGER NOT NULL DEFAULT 0",
                "status": "TEXT NOT NULL DEFAULT 'draft'",
                "message_preview": "TEXT",
                "updated_at": "REAL NOT NULL DEFAULT 0",
                "revision": "INTEGER NOT NULL DEFAULT 0",
            },
            "autonomy_inbox_items": {
                "message_preview": "TEXT",
                "importance": "TEXT NOT NULL DEFAULT 'normal'",
                "category": "TEXT NOT NULL DEFAULT 'utility'",
                "approval_required": "INTEGER NOT NULL DEFAULT 0",
                "status": "TEXT NOT NULL DEFAULT 'pending'",
                "seen_at": "REAL",
                "last_delivered_at": "REAL",
                "updated_at": "REAL NOT NULL DEFAULT 0",
                "revision": "INTEGER NOT NULL DEFAULT 0",
            },
            "autonomy_delivery_attempts": {
                "message_text": "TEXT",
                "target_platform": "TEXT",
                "target_chat_id": "TEXT",
                "target_thread_id": "TEXT",
                "sent_at": "REAL",
                "error": "TEXT",
            },
            "autonomy_state": {
                "last_supervisor_run_at": "REAL",
                "last_home_delivery_at": "REAL",
                "last_social_nudge_at": "REAL",
            },
        }

        for table_name, columns in required_columns.items():
            try:
                rows = cursor.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            except sqlite3.OperationalError:
                continue
            existing = {row[1] for row in rows}
            for column_name, column_type in columns.items():
                if column_name in existing:
                    continue
                safe_table = table_name.replace('"', '""')
                safe_column = column_name.replace('"', '""')
                cursor.execute(
                    f'ALTER TABLE "{safe_table}" ADD COLUMN "{safe_column}" {column_type}'
                )

    def close(self):
        """Close the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # =========================================================================
    # Autonomy state
    # =========================================================================

    def _next_autonomy_revision(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT current_revision FROM autonomy_state WHERE singleton_id = 1"
        ).fetchone()
        current = int(row["current_revision"]) if row and row["current_revision"] is not None else 0
        new_revision = current + 1
        conn.execute(
            "UPDATE autonomy_state SET current_revision = ? WHERE singleton_id = 1",
            (new_revision,),
        )
        return new_revision

    def get_autonomy_state(self) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM autonomy_state WHERE singleton_id = 1"
            ).fetchone()
        return dict(row) if row else {
            "singleton_id": 1,
            "current_revision": 0,
            "paused": 0,
            "last_supervisor_run_at": None,
            "last_home_delivery_at": None,
            "last_social_nudge_at": None,
        }

    def set_autonomy_paused(self, paused: bool) -> None:
        def _do(conn):
            conn.execute(
                "UPDATE autonomy_state SET paused = ? WHERE singleton_id = 1",
                (1 if paused else 0,),
            )
        self._execute_write(_do)

    def mark_autonomy_supervisor_run(self, ran_at: Optional[float] = None) -> None:
        ran_at = ran_at or time.time()

        def _do(conn):
            conn.execute(
                "UPDATE autonomy_state SET last_supervisor_run_at = ? WHERE singleton_id = 1",
                (ran_at,),
            )
        self._execute_write(_do)

    def mark_autonomy_delivery(self, *, social: bool = False, delivered_at: Optional[float] = None) -> None:
        delivered_at = delivered_at or time.time()

        def _do(conn):
            if social:
                conn.execute(
                    "UPDATE autonomy_state SET last_home_delivery_at = ?, last_social_nudge_at = ? WHERE singleton_id = 1",
                    (delivered_at, delivered_at),
                )
            else:
                conn.execute(
                    "UPDATE autonomy_state SET last_home_delivery_at = ? WHERE singleton_id = 1",
                    (delivered_at,),
                )
        self._execute_write(_do)

    def create_autonomy_run(
        self,
        run_type: str,
        *,
        session_key: str = None,
        session_id: str = None,
        status: str = "running",
        summary: str = None,
        payload: Dict[str, Any] | None = None,
    ) -> int:
        payload_json = json.dumps(payload or {})

        def _do(conn):
            cursor = conn.execute(
                """INSERT INTO autonomy_runs
                   (run_type, session_key, session_id, status, summary, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_type, session_key, session_id, status, summary, payload_json, time.time()),
            )
            return cursor.lastrowid

        return self._execute_write(_do)

    def finish_autonomy_run(
        self,
        run_id: int,
        *,
        status: str,
        summary: str = None,
        error: str = None,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        payload_json = json.dumps(payload or {})

        def _do(conn):
            conn.execute(
                """UPDATE autonomy_runs
                   SET status = ?, summary = COALESCE(?, summary), error = ?, payload = ?, finished_at = ?
                   WHERE id = ?""",
                (status, summary, error, payload_json, time.time(), run_id),
            )

        self._execute_write(_do)

    def upsert_autonomy_watch_item(
        self,
        *,
        normalized_key: str,
        title: str,
        kind: str,
        description: str = "",
        importance: str = "normal",
        source_session_key: str = None,
        source_message_ref: str = None,
        inference_mode: str = "implied",
        due_at: Optional[float] = None,
        next_check_at: Optional[float] = None,
        metadata: Dict[str, Any] | None = None,
        status: str = "active",
    ) -> Dict[str, Any]:
        now_ts = time.time()
        metadata_json = json.dumps(metadata or {})

        def _do(conn):
            existing = conn.execute(
                """SELECT id, title, kind, description, importance, source_session_key,
                          source_message_ref, inference_mode, due_at, status, next_check_at, metadata, revision
                   FROM autonomy_watch_items WHERE normalized_key = ?""",
                (normalized_key,),
            ).fetchone()
            if existing:
                changed = any([
                    existing["title"] != title,
                    existing["kind"] != kind,
                    (existing["description"] or "") != (description or ""),
                    (existing["importance"] or "normal") != importance,
                    (existing["source_session_key"] or "") != (source_session_key or ""),
                    (existing["source_message_ref"] or "") != (source_message_ref or ""),
                    (existing["inference_mode"] or "implied") != inference_mode,
                    existing["due_at"] != due_at,
                    (existing["status"] or "active") != status,
                    existing["next_check_at"] != next_check_at,
                    (existing["metadata"] or "{}") != metadata_json,
                ])
                if not changed:
                    return {"id": existing["id"], "revision": existing["revision"], "changed": False}
                revision = self._next_autonomy_revision(conn)
                conn.execute(
                    """UPDATE autonomy_watch_items
                       SET title = ?, kind = ?, description = ?, importance = ?, source_session_key = ?,
                           source_message_ref = ?, inference_mode = ?, due_at = ?, status = ?, next_check_at = ?,
                           last_changed_at = ?, metadata = ?, revision = ?
                       WHERE normalized_key = ?""",
                    (
                        title, kind, description, importance, source_session_key, source_message_ref,
                        inference_mode, due_at, status, next_check_at, now_ts, metadata_json, revision, normalized_key,
                    ),
                )
                return {"id": existing["id"], "revision": revision, "changed": True}

            revision = self._next_autonomy_revision(conn)
            cursor = conn.execute(
                """INSERT INTO autonomy_watch_items
                   (normalized_key, kind, title, description, importance, source_session_key,
                    source_message_ref, inference_mode, due_at, status, next_check_at,
                    last_changed_at, metadata, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalized_key, kind, title, description, importance, source_session_key,
                    source_message_ref, inference_mode, due_at, status, next_check_at,
                    now_ts, metadata_json, revision,
                ),
            )
            return {"id": cursor.lastrowid, "revision": revision, "changed": True}

        return self._execute_write(_do)

    def update_autonomy_watch_item(
        self,
        normalized_key: str,
        *,
        status: Optional[str] = None,
        next_check_at: Optional[float] = None,
        description: Optional[str] = None,
        importance: Optional[str] = None,
        last_checked_at: Optional[float] = None,
    ) -> None:
        def _do(conn):
            existing = conn.execute(
                "SELECT id FROM autonomy_watch_items WHERE normalized_key = ?",
                (normalized_key,),
            ).fetchone()
            if not existing:
                return
            assignments = []
            params: list[Any] = []
            if status is not None:
                assignments.append("status = ?")
                params.append(status)
            if next_check_at is not None:
                assignments.append("next_check_at = ?")
                params.append(next_check_at)
            if description is not None:
                assignments.append("description = ?")
                params.append(description)
            if importance is not None:
                assignments.append("importance = ?")
                params.append(importance)
            if last_checked_at is not None:
                assignments.append("last_checked_at = ?")
                params.append(last_checked_at)
            if not assignments:
                return
            revision = self._next_autonomy_revision(conn)
            assignments.extend(["last_changed_at = ?", "revision = ?"])
            params.extend([time.time(), revision, normalized_key])
            conn.execute(
                f"UPDATE autonomy_watch_items SET {', '.join(assignments)} WHERE normalized_key = ?",
                tuple(params),
            )

        self._execute_write(_do)

    def list_autonomy_watch_items(self, *, statuses: Optional[List[str]] = None, limit: int = 50) -> List[Dict[str, Any]]:
        query = (
            "SELECT id, normalized_key, kind, title, description, importance, source_session_key, "
            "source_message_ref, inference_mode, due_at, status, next_check_at, last_checked_at, "
            "last_changed_at, metadata, revision "
            "FROM autonomy_watch_items"
        )
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY importance DESC, last_changed_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["metadata"] = {}
        return items

    def list_due_autonomy_watch_items(self, *, now_ts: Optional[float] = None, limit: int = 20) -> List[Dict[str, Any]]:
        now_ts = now_ts or time.time()
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, normalized_key, kind, title, description, importance, source_session_key,
                          source_message_ref, inference_mode, due_at, status, next_check_at, last_checked_at,
                          last_changed_at, metadata, revision
                   FROM autonomy_watch_items
                   WHERE status = 'active' AND (next_check_at IS NULL OR next_check_at <= ?)
                   ORDER BY COALESCE(next_check_at, 0) ASC, last_changed_at DESC
                   LIMIT ?""",
                (now_ts, limit),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["metadata"] = {}
        return items

    def prune_autonomy_resolved(self, *, older_than_ts: float) -> Dict[str, int]:
        """Delete resolved autonomy records older than the retention cutoff."""
        cutoff = float(older_than_ts)

        def _do(conn):
            watch_rows = conn.execute(
                """SELECT id FROM autonomy_watch_items
                   WHERE status = 'resolved' AND last_changed_at < ?""",
                (cutoff,),
            ).fetchall()
            watch_ids = [int(row["id"]) for row in watch_rows]
            if not watch_ids:
                return {
                    "watch_items": 0,
                    "findings": 0,
                    "artifacts": 0,
                    "inbox_items": 0,
                    "delivery_attempts": 0,
                }

            watch_placeholders = ",".join("?" for _ in watch_ids)
            finding_ids = [
                int(row["id"])
                for row in conn.execute(
                    f"SELECT id FROM autonomy_findings WHERE watch_item_id IN ({watch_placeholders})",
                    tuple(watch_ids),
                ).fetchall()
            ]
            artifact_ids = [
                int(row["id"])
                for row in conn.execute(
                    f"SELECT id FROM autonomy_artifacts WHERE watch_item_id IN ({watch_placeholders})",
                    tuple(watch_ids),
                ).fetchall()
            ]

            inbox_ids: list[int] = []
            if finding_ids:
                finding_placeholders = ",".join("?" for _ in finding_ids)
                inbox_ids.extend(
                    int(row["id"])
                    for row in conn.execute(
                        f"""SELECT id FROM autonomy_inbox_items
                            WHERE source_type = 'finding' AND source_id IN ({finding_placeholders})""",
                        tuple(finding_ids),
                    ).fetchall()
                )
            if artifact_ids:
                artifact_placeholders = ",".join("?" for _ in artifact_ids)
                inbox_ids.extend(
                    int(row["id"])
                    for row in conn.execute(
                        f"""SELECT id FROM autonomy_inbox_items
                            WHERE source_type = 'artifact' AND source_id IN ({artifact_placeholders})""",
                        tuple(artifact_ids),
                    ).fetchall()
                )

            delivery_count = 0
            if inbox_ids:
                inbox_placeholders = ",".join("?" for _ in inbox_ids)
                delivery_count = conn.execute(
                    f"DELETE FROM autonomy_delivery_attempts WHERE inbox_item_id IN ({inbox_placeholders})",
                    tuple(inbox_ids),
                ).rowcount
                inbox_count = conn.execute(
                    f"DELETE FROM autonomy_inbox_items WHERE id IN ({inbox_placeholders})",
                    tuple(inbox_ids),
                ).rowcount
            else:
                inbox_count = 0

            finding_count = 0
            if finding_ids:
                finding_placeholders = ",".join("?" for _ in finding_ids)
                finding_count = conn.execute(
                    f"DELETE FROM autonomy_findings WHERE id IN ({finding_placeholders})",
                    tuple(finding_ids),
                ).rowcount

            artifact_count = 0
            if artifact_ids:
                artifact_placeholders = ",".join("?" for _ in artifact_ids)
                artifact_count = conn.execute(
                    f"DELETE FROM autonomy_artifacts WHERE id IN ({artifact_placeholders})",
                    tuple(artifact_ids),
                ).rowcount

            watch_count = conn.execute(
                f"DELETE FROM autonomy_watch_items WHERE id IN ({watch_placeholders})",
                tuple(watch_ids),
            ).rowcount

            return {
                "watch_items": int(watch_count or 0),
                "findings": int(finding_count or 0),
                "artifacts": int(artifact_count or 0),
                "inbox_items": int(inbox_count or 0),
                "delivery_attempts": int(delivery_count or 0),
            }

        return self._execute_write(_do)

    def add_autonomy_finding(
        self,
        *,
        run_id: Optional[int],
        watch_item_id: Optional[int],
        kind: str,
        title: str,
        summary: str,
        details: Dict[str, Any] | None = None,
        importance: str = "normal",
        category: str = "utility",
        message_preview: str = "",
    ) -> Dict[str, Any]:
        details_json = json.dumps(details or {})
        created_at = time.time()

        def _do(conn):
            revision = self._next_autonomy_revision(conn)
            cursor = conn.execute(
                """INSERT INTO autonomy_findings
                   (run_id, watch_item_id, kind, title, summary, details, importance, category, message_preview, created_at, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, watch_item_id, kind, title, summary, details_json, importance, category, message_preview, created_at, revision),
            )
            return {"id": cursor.lastrowid, "revision": revision}

        return self._execute_write(_do)

    def add_autonomy_artifact(
        self,
        *,
        run_id: Optional[int],
        watch_item_id: Optional[int],
        artifact_type: str,
        title: str,
        summary: str,
        payload: Dict[str, Any] | None = None,
        target: Dict[str, Any] | None = None,
        execution_requirements: Dict[str, Any] | None = None,
        importance: str = "normal",
        category: str = "utility",
        approval_required: bool = False,
        message_preview: str = "",
        status: str = "draft",
    ) -> Dict[str, Any]:
        now_ts = time.time()
        payload_json = json.dumps(payload or {})
        target_json = json.dumps(target or {})
        requirements_json = json.dumps(execution_requirements or {})

        def _do(conn):
            revision = self._next_autonomy_revision(conn)
            cursor = conn.execute(
                """INSERT INTO autonomy_artifacts
                   (run_id, watch_item_id, artifact_type, title, summary, payload, target,
                    execution_requirements, importance, category, approval_required, status,
                    message_preview, created_at, updated_at, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, watch_item_id, artifact_type, title, summary, payload_json, target_json,
                    requirements_json, importance, category, 1 if approval_required else 0, status,
                    message_preview, now_ts, now_ts, revision,
                ),
            )
            return {"id": cursor.lastrowid, "revision": revision}

        return self._execute_write(_do)

    def list_autonomy_artifacts(
        self,
        *,
        statuses: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = (
            "SELECT id, run_id, watch_item_id, artifact_type, title, summary, payload, target, "
            "execution_requirements, importance, category, approval_required, status, "
            "message_preview, created_at, updated_at, revision "
            "FROM autonomy_artifacts"
        )
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY revision DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            for key in ("payload", "target", "execution_requirements"):
                try:
                    item[key] = json.loads(item.get(key) or "{}")
                except (TypeError, json.JSONDecodeError):
                    item[key] = {}
        return items

    def upsert_autonomy_inbox_item(
        self,
        *,
        source_type: str,
        source_id: int,
        title: str,
        message_preview: str,
        importance: str = "normal",
        category: str = "utility",
        approval_required: bool = False,
        status: str = "pending",
    ) -> Dict[str, Any]:
        now_ts = time.time()

        def _do(conn):
            existing = conn.execute(
                """SELECT id, title, message_preview, importance, category, approval_required, status, revision
                   FROM autonomy_inbox_items
                   WHERE source_type = ? AND source_id = ?""",
                (source_type, source_id),
            ).fetchone()
            if existing:
                changed = any([
                    (existing["title"] or "") != title,
                    (existing["message_preview"] or "") != message_preview,
                    (existing["importance"] or "normal") != importance,
                    (existing["category"] or "utility") != category,
                    bool(existing["approval_required"]) != bool(approval_required),
                    (existing["status"] or "pending") != status,
                ])
                if not changed:
                    return {"id": existing["id"], "revision": existing["revision"], "changed": False}
                revision = self._next_autonomy_revision(conn)
                conn.execute(
                    """UPDATE autonomy_inbox_items
                       SET title = ?, message_preview = ?, importance = ?, category = ?, approval_required = ?,
                           status = ?, updated_at = ?, revision = ?
                       WHERE source_type = ? AND source_id = ?""",
                    (
                        title, message_preview, importance, category, 1 if approval_required else 0,
                        status, now_ts, revision, source_type, source_id,
                    ),
                )
                return {"id": existing["id"], "revision": revision, "changed": True}

            revision = self._next_autonomy_revision(conn)
            cursor = conn.execute(
                """INSERT INTO autonomy_inbox_items
                   (source_type, source_id, title, message_preview, importance, category, approval_required,
                    status, created_at, updated_at, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_type, source_id, title, message_preview, importance, category, 1 if approval_required else 0,
                    status, now_ts, now_ts, revision,
                ),
            )
            return {"id": cursor.lastrowid, "revision": revision, "changed": True}

        return self._execute_write(_do)

    def list_autonomy_inbox_items(
        self,
        *,
        statuses: Optional[List[str]] = None,
        since_revision: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = (
            "SELECT id, source_type, source_id, title, message_preview, importance, category, approval_required, "
            "status, seen_at, last_delivered_at, created_at, updated_at, revision "
            "FROM autonomy_inbox_items"
        )
        clauses: list[str] = []
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if since_revision is not None:
            clauses.append("revision > ?")
            params.append(int(since_revision))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY revision DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def mark_autonomy_inbox_items_seen(
        self,
        item_ids: List[int],
        *,
        delivered_at: Optional[float] = None,
    ) -> None:
        ids = [int(item_id) for item_id in item_ids if item_id is not None]
        if not ids:
            return
        delivered_at = delivered_at or time.time()

        def _do(conn):
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""UPDATE autonomy_inbox_items
                    SET status = 'seen',
                        seen_at = COALESCE(seen_at, ?),
                        last_delivered_at = ?
                    WHERE id IN ({placeholders})""",
                (delivered_at, delivered_at, *ids),
            )

        self._execute_write(_do)

    def record_autonomy_delivery_attempt(
        self,
        *,
        inbox_item_id: int,
        mode: str,
        status: str,
        message_text: str = "",
        target_platform: str = "",
        target_chat_id: str = "",
        target_thread_id: str = "",
        error: str = "",
        sent_at: Optional[float] = None,
    ) -> int:
        created_at = time.time()
        sent_value = sent_at if sent_at is not None else (created_at if status == "sent" else None)

        def _do(conn):
            cursor = conn.execute(
                """INSERT INTO autonomy_delivery_attempts
                   (inbox_item_id, mode, status, message_text, target_platform, target_chat_id,
                    target_thread_id, created_at, sent_at, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    inbox_item_id, mode, status, message_text, target_platform, target_chat_id,
                    target_thread_id, created_at, sent_value, error,
                ),
            )
            return cursor.lastrowid

        return self._execute_write(_do)

    def list_autonomy_runs(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, run_type, session_key, session_id, status, summary,
                          payload, created_at, finished_at, error
                   FROM autonomy_runs
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["payload"] = {}
        return items

    def get_autonomy_status_counts(self) -> Dict[str, int]:
        with self._lock:
            pending = self._conn.execute(
                "SELECT COUNT(*) AS count FROM autonomy_inbox_items WHERE status = 'pending'"
            ).fetchone()["count"]
            active_watch = self._conn.execute(
                "SELECT COUNT(*) AS count FROM autonomy_watch_items WHERE status = 'active'"
            ).fetchone()["count"]
            draft_artifacts = self._conn.execute(
                "SELECT COUNT(*) AS count FROM autonomy_artifacts WHERE status = 'draft'"
            ).fetchone()["count"]
        return {
            "pending_inbox": int(pending or 0),
            "active_watch_items": int(active_watch or 0),
            "draft_artifacts": int(draft_artifacts or 0),
        }

    # =========================================================================
    # Session lifecycle
    # =========================================================================

    def create_session(
        self,
        session_id: str,
        source: str,
        model: str = None,
        model_config: Dict[str, Any] = None,
        system_prompt: str = None,
        user_id: str = None,
        parent_session_id: str = None,
    ) -> str:
        """Create a new session record. Returns the session_id."""
        def _do(conn):
            conn.execute(
                """INSERT OR IGNORE INTO sessions (id, source, user_id, model, model_config,
                   system_prompt, parent_session_id, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    source,
                    user_id,
                    model,
                    json.dumps(model_config) if model_config else None,
                    system_prompt,
                    parent_session_id,
                    time.time(),
                ),
            )
        self._execute_write(_do)
        return session_id

    def end_session(self, session_id: str, end_reason: str) -> None:
        """Mark a session as ended."""
        def _do(conn):
            conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?",
                (time.time(), end_reason, session_id),
            )
        self._execute_write(_do)

    def reopen_session(self, session_id: str) -> None:
        """Clear ended_at/end_reason so a session can be resumed."""
        def _do(conn):
            conn.execute(
                "UPDATE sessions SET ended_at = NULL, end_reason = NULL WHERE id = ?",
                (session_id,),
            )
        self._execute_write(_do)

    def update_system_prompt(self, session_id: str, system_prompt: str) -> None:
        """Store the full assembled system prompt snapshot."""
        def _do(conn):
            conn.execute(
                "UPDATE sessions SET system_prompt = ? WHERE id = ?",
                (system_prompt, session_id),
            )
        self._execute_write(_do)

    def update_token_counts(
        self,
        session_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        estimated_cost_usd: Optional[float] = None,
        actual_cost_usd: Optional[float] = None,
        cost_status: Optional[str] = None,
        cost_source: Optional[str] = None,
        pricing_version: Optional[str] = None,
        billing_provider: Optional[str] = None,
        billing_base_url: Optional[str] = None,
        billing_mode: Optional[str] = None,
        absolute: bool = False,
    ) -> None:
        """Update token counters and backfill model if not already set.

        When *absolute* is False (default), values are **incremented** — use
        this for per-API-call deltas (CLI path).

        When *absolute* is True, values are **set directly** — use this when
        the caller already holds cumulative totals (gateway path, where the
        cached agent accumulates across messages).
        """
        if absolute:
            sql = """UPDATE sessions SET
                   input_tokens = ?,
                   output_tokens = ?,
                   cache_read_tokens = ?,
                   cache_write_tokens = ?,
                   reasoning_tokens = ?,
                   estimated_cost_usd = COALESCE(?, 0),
                   actual_cost_usd = CASE
                       WHEN ? IS NULL THEN actual_cost_usd
                       ELSE ?
                   END,
                   cost_status = COALESCE(?, cost_status),
                   cost_source = COALESCE(?, cost_source),
                   pricing_version = COALESCE(?, pricing_version),
                   billing_provider = COALESCE(billing_provider, ?),
                   billing_base_url = COALESCE(billing_base_url, ?),
                   billing_mode = COALESCE(billing_mode, ?),
                   model = COALESCE(model, ?)
                   WHERE id = ?"""
        else:
            sql = """UPDATE sessions SET
                   input_tokens = input_tokens + ?,
                   output_tokens = output_tokens + ?,
                   cache_read_tokens = cache_read_tokens + ?,
                   cache_write_tokens = cache_write_tokens + ?,
                   reasoning_tokens = reasoning_tokens + ?,
                   estimated_cost_usd = COALESCE(estimated_cost_usd, 0) + COALESCE(?, 0),
                   actual_cost_usd = CASE
                       WHEN ? IS NULL THEN actual_cost_usd
                       ELSE COALESCE(actual_cost_usd, 0) + ?
                   END,
                   cost_status = COALESCE(?, cost_status),
                   cost_source = COALESCE(?, cost_source),
                   pricing_version = COALESCE(?, pricing_version),
                   billing_provider = COALESCE(billing_provider, ?),
                   billing_base_url = COALESCE(billing_base_url, ?),
                   billing_mode = COALESCE(billing_mode, ?),
                   model = COALESCE(model, ?)
                   WHERE id = ?"""
        params = (
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            reasoning_tokens,
            estimated_cost_usd,
            actual_cost_usd,
            actual_cost_usd,
            cost_status,
            cost_source,
            pricing_version,
            billing_provider,
            billing_base_url,
            billing_mode,
            model,
            session_id,
        )
        def _do(conn):
            conn.execute(sql, params)
        self._execute_write(_do)

    def ensure_session(
        self,
        session_id: str,
        source: str = "unknown",
        model: str = None,
    ) -> None:
        """Ensure a session row exists, creating it with minimal metadata if absent.

        Used by _flush_messages_to_session_db to recover from a failed
        create_session() call (e.g. transient SQLite lock at agent startup).
        INSERT OR IGNORE is safe to call even when the row already exists.
        """
        def _do(conn):
            conn.execute(
                """INSERT OR IGNORE INTO sessions
                   (id, source, model, started_at)
                   VALUES (?, ?, ?, ?)""",
                (session_id, source, model, time.time()),
            )
        self._execute_write(_do)

    def set_token_counts(
        self,
        session_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        estimated_cost_usd: Optional[float] = None,
        actual_cost_usd: Optional[float] = None,
        cost_status: Optional[str] = None,
        cost_source: Optional[str] = None,
        pricing_version: Optional[str] = None,
        billing_provider: Optional[str] = None,
        billing_base_url: Optional[str] = None,
        billing_mode: Optional[str] = None,
    ) -> None:
        """Set token counters to absolute values (not increment).

        Use this when the caller provides cumulative totals from a completed
        conversation run (e.g. the gateway, where the cached agent's
        session_prompt_tokens already reflects the running total).
        """
        def _do(conn):
            conn.execute(
                """UPDATE sessions SET
                   input_tokens = ?,
                   output_tokens = ?,
                   cache_read_tokens = ?,
                   cache_write_tokens = ?,
                   reasoning_tokens = ?,
                   estimated_cost_usd = ?,
                   actual_cost_usd = CASE
                       WHEN ? IS NULL THEN actual_cost_usd
                       ELSE ?
                   END,
                   cost_status = COALESCE(?, cost_status),
                   cost_source = COALESCE(?, cost_source),
                   pricing_version = COALESCE(?, pricing_version),
                   billing_provider = COALESCE(billing_provider, ?),
                   billing_base_url = COALESCE(billing_base_url, ?),
                   billing_mode = COALESCE(billing_mode, ?),
                   model = COALESCE(model, ?)
                   WHERE id = ?""",
                (
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    reasoning_tokens,
                    estimated_cost_usd,
                    actual_cost_usd,
                    actual_cost_usd,
                    cost_status,
                    cost_source,
                    pricing_version,
                    billing_provider,
                    billing_base_url,
                    billing_mode,
                    model,
                    session_id,
                ),
            )
        self._execute_write(_do)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get a session by ID."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def resolve_session_id(self, session_id_or_prefix: str) -> Optional[str]:
        """Resolve an exact or uniquely prefixed session ID to the full ID.

        Returns the exact ID when it exists. Otherwise treats the input as a
        prefix and returns the single matching session ID if the prefix is
        unambiguous. Returns None for no matches or ambiguous prefixes.
        """
        exact = self.get_session(session_id_or_prefix)
        if exact:
            return exact["id"]

        escaped = (
            session_id_or_prefix
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id FROM sessions WHERE id LIKE ? ESCAPE '\\' ORDER BY started_at DESC LIMIT 2",
                (f"{escaped}%",),
            )
            matches = [row["id"] for row in cursor.fetchall()]
        if len(matches) == 1:
            return matches[0]
        return None

    # Maximum length for session titles
    MAX_TITLE_LENGTH = 100

    @staticmethod
    def sanitize_title(title: Optional[str]) -> Optional[str]:
        """Validate and sanitize a session title.

        - Strips leading/trailing whitespace
        - Removes ASCII control characters (0x00-0x1F, 0x7F) and problematic
          Unicode control chars (zero-width, RTL/LTR overrides, etc.)
        - Collapses internal whitespace runs to single spaces
        - Normalizes empty/whitespace-only strings to None
        - Enforces MAX_TITLE_LENGTH

        Returns the cleaned title string or None.
        Raises ValueError if the title exceeds MAX_TITLE_LENGTH after cleaning.
        """
        if not title:
            return None

        # Remove ASCII control characters (0x00-0x1F, 0x7F) but keep
        # whitespace chars (\t=0x09, \n=0x0A, \r=0x0D) so they can be
        # normalized to spaces by the whitespace collapsing step below
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', title)

        # Remove problematic Unicode control characters:
        # - Zero-width chars (U+200B-U+200F, U+FEFF)
        # - Directional overrides (U+202A-U+202E, U+2066-U+2069)
        # - Object replacement (U+FFFC), interlinear annotation (U+FFF9-U+FFFB)
        cleaned = re.sub(
            r'[\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufffc\ufff9-\ufffb]',
            '', cleaned,
        )

        # Collapse internal whitespace runs and strip
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        if not cleaned:
            return None

        if len(cleaned) > SessionDB.MAX_TITLE_LENGTH:
            raise ValueError(
                f"Title too long ({len(cleaned)} chars, max {SessionDB.MAX_TITLE_LENGTH})"
            )

        return cleaned

    def set_session_title(self, session_id: str, title: str) -> bool:
        """Set or update a session's title.

        Returns True if session was found and title was set.
        Raises ValueError if title is already in use by another session,
        or if the title fails validation (too long, invalid characters).
        Empty/whitespace-only strings are normalized to None (clearing the title).
        """
        title = self.sanitize_title(title)
        def _do(conn):
            if title:
                # Check uniqueness (allow the same session to keep its own title)
                cursor = conn.execute(
                    "SELECT id FROM sessions WHERE title = ? AND id != ?",
                    (title, session_id),
                )
                conflict = cursor.fetchone()
                if conflict:
                    raise ValueError(
                        f"Title '{title}' is already in use by session {conflict['id']}"
                    )
            cursor = conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )
            return cursor.rowcount
        rowcount = self._execute_write(_do)
        return rowcount > 0

    def get_session_title(self, session_id: str) -> Optional[str]:
        """Get the title for a session, or None."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT title FROM sessions WHERE id = ?", (session_id,)
            )
            row = cursor.fetchone()
        return row["title"] if row else None

    def get_session_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Look up a session by exact title. Returns session dict or None."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM sessions WHERE title = ?", (title,)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def resolve_session_by_title(self, title: str) -> Optional[str]:
        """Resolve a title to a session ID, preferring the latest in a lineage.

        If the exact title exists, returns that session's ID.
        If not, searches for "title #N" variants and returns the latest one.
        If the exact title exists AND numbered variants exist, returns the
        latest numbered variant (the most recent continuation).
        """
        # First try exact match
        exact = self.get_session_by_title(title)

        # Also search for numbered variants: "title #2", "title #3", etc.
        # Escape SQL LIKE wildcards (%, _) in the title to prevent false matches
        escaped = title.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, title, started_at FROM sessions "
                "WHERE title LIKE ? ESCAPE '\\' ORDER BY started_at DESC",
                (f"{escaped} #%",),
            )
            numbered = cursor.fetchall()

        if numbered:
            # Return the most recent numbered variant
            return numbered[0]["id"]
        elif exact:
            return exact["id"]
        return None

    def get_next_title_in_lineage(self, base_title: str) -> str:
        """Generate the next title in a lineage (e.g., "my session" → "my session #2").

        Strips any existing " #N" suffix to find the base name, then finds
        the highest existing number and increments.
        """
        # Strip existing #N suffix to find the true base
        match = re.match(r'^(.*?) #(\d+)$', base_title)
        if match:
            base = match.group(1)
        else:
            base = base_title

        # Find all existing numbered variants
        # Escape SQL LIKE wildcards (%, _) in the base to prevent false matches
        escaped = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            cursor = self._conn.execute(
                "SELECT title FROM sessions WHERE title = ? OR title LIKE ? ESCAPE '\\'",
                (base, f"{escaped} #%"),
            )
            existing = [row["title"] for row in cursor.fetchall()]

        if not existing:
            return base  # No conflict, use the base name as-is

        # Find the highest number
        max_num = 1  # The unnumbered original counts as #1
        for t in existing:
            m = re.match(r'^.* #(\d+)$', t)
            if m:
                max_num = max(max_num, int(m.group(1)))

        return f"{base} #{max_num + 1}"

    def list_sessions_rich(
        self,
        source: str = None,
        exclude_sources: List[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List sessions with preview (first user message) and last active timestamp.

        Returns dicts with keys: id, source, model, title, started_at, ended_at,
        message_count, preview (first 60 chars of first user message),
        last_active (timestamp of last message).

        Uses a single query with correlated subqueries instead of N+2 queries.
        """
        where_clauses = []
        params = []

        if source:
            where_clauses.append("s.source = ?")
            params.append(source)
        if exclude_sources:
            placeholders = ",".join("?" for _ in exclude_sources)
            where_clauses.append(f"s.source NOT IN ({placeholders})")
            params.extend(exclude_sources)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"""
            SELECT s.*,
                COALESCE(
                    (SELECT SUBSTR(REPLACE(REPLACE(m.content, X'0A', ' '), X'0D', ' '), 1, 63)
                     FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'user' AND m.content IS NOT NULL
                     ORDER BY m.timestamp, m.id LIMIT 1),
                    ''
                ) AS _preview_raw,
                COALESCE(
                    (SELECT MAX(m2.timestamp) FROM messages m2 WHERE m2.session_id = s.id),
                    s.started_at
                ) AS last_active
            FROM sessions s
            {where_sql}
            ORDER BY s.started_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
        sessions = []
        for row in rows:
            s = dict(row)
            # Build the preview from the raw substring
            raw = s.pop("_preview_raw", "").strip()
            if raw:
                text = raw[:60]
                s["preview"] = text + ("..." if len(raw) > 60 else "")
            else:
                s["preview"] = ""
            sessions.append(s)

        return sessions

    # =========================================================================
    # Message storage
    # =========================================================================

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str = None,
        tool_name: str = None,
        tool_calls: Any = None,
        tool_call_id: str = None,
        token_count: int = None,
        finish_reason: str = None,
        reasoning: str = None,
        reasoning_details: Any = None,
        codex_reasoning_items: Any = None,
    ) -> int:
        """
        Append a message to a session. Returns the message row ID.

        Also increments the session's message_count (and tool_call_count
        if role is 'tool' or tool_calls is present).
        """
        # Serialize structured fields to JSON before entering the write txn
        reasoning_details_json = (
            json.dumps(reasoning_details)
            if reasoning_details else None
        )
        codex_items_json = (
            json.dumps(codex_reasoning_items)
            if codex_reasoning_items else None
        )
        tool_calls_json = json.dumps(tool_calls) if tool_calls else None

        # Pre-compute tool call count
        num_tool_calls = 0
        if tool_calls is not None:
            num_tool_calls = len(tool_calls) if isinstance(tool_calls, list) else 1

        def _do(conn):
            cursor = conn.execute(
                """INSERT INTO messages (session_id, role, content, tool_call_id,
                   tool_calls, tool_name, timestamp, token_count, finish_reason,
                   reasoning, reasoning_details, codex_reasoning_items)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    role,
                    content,
                    tool_call_id,
                    tool_calls_json,
                    tool_name,
                    time.time(),
                    token_count,
                    finish_reason,
                    reasoning,
                    reasoning_details_json,
                    codex_items_json,
                ),
            )
            msg_id = cursor.lastrowid

            # Update counters
            if num_tool_calls > 0:
                conn.execute(
                    """UPDATE sessions SET message_count = message_count + 1,
                       tool_call_count = tool_call_count + ? WHERE id = ?""",
                    (num_tool_calls, session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
                    (session_id,),
                )
            return msg_id

        return self._execute_write(_do)

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Load all messages for a session, ordered by timestamp."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp, id",
                (session_id,),
            )
            rows = cursor.fetchall()
        result = []
        for row in rows:
            msg = dict(row)
            if msg.get("tool_calls"):
                try:
                    msg["tool_calls"] = json.loads(msg["tool_calls"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(msg)
        return result

    def get_messages_as_conversation(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Load messages in the OpenAI conversation format (role + content dicts).
        Used by the gateway to restore conversation history.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT role, content, tool_call_id, tool_calls, tool_name, "
                "reasoning, reasoning_details, codex_reasoning_items "
                "FROM messages WHERE session_id = ? ORDER BY timestamp, id",
                (session_id,),
            )
            rows = cursor.fetchall()
        messages = []
        for row in rows:
            msg = {"role": row["role"], "content": row["content"]}
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            if row["tool_name"]:
                msg["tool_name"] = row["tool_name"]
            if row["tool_calls"]:
                try:
                    msg["tool_calls"] = json.loads(row["tool_calls"])
                except (json.JSONDecodeError, TypeError):
                    pass
            # Restore reasoning fields on assistant messages so providers
            # that replay reasoning (OpenRouter, OpenAI, Nous) receive
            # coherent multi-turn reasoning context.
            if row["role"] == "assistant":
                if row["reasoning"]:
                    msg["reasoning"] = row["reasoning"]
                if row["reasoning_details"]:
                    try:
                        msg["reasoning_details"] = json.loads(row["reasoning_details"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                if row["codex_reasoning_items"]:
                    try:
                        msg["codex_reasoning_items"] = json.loads(row["codex_reasoning_items"])
                    except (json.JSONDecodeError, TypeError):
                        pass
            messages.append(msg)
        return messages

    # =========================================================================
    # Search
    # =========================================================================

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        """Sanitize user input for safe use in FTS5 MATCH queries.

        FTS5 has its own query syntax where characters like ``"``, ``(``, ``)``,
        ``+``, ``*``, ``{``, ``}`` and bare boolean operators (``AND``, ``OR``,
        ``NOT``) have special meaning.  Passing raw user input directly to
        MATCH can cause ``sqlite3.OperationalError``.

        Strategy:
        - Preserve properly paired quoted phrases (``"exact phrase"``)
        - Strip unmatched FTS5-special characters that would cause errors
        - Wrap unquoted hyphenated and dotted terms in quotes so FTS5
          matches them as exact phrases instead of splitting on the
          hyphen/dot (e.g. ``chat-send``, ``P2.2``, ``my-app.config.ts``)
        """
        # Step 1: Extract balanced double-quoted phrases and protect them
        # from further processing via numbered placeholders.
        _quoted_parts: list = []

        def _preserve_quoted(m: re.Match) -> str:
            _quoted_parts.append(m.group(0))
            return f"\x00Q{len(_quoted_parts) - 1}\x00"

        sanitized = re.sub(r'"[^"]*"', _preserve_quoted, query)

        # Step 2: Strip remaining (unmatched) FTS5-special characters
        sanitized = re.sub(r'[+{}()\"^]', " ", sanitized)

        # Step 3: Collapse repeated * (e.g. "***") into a single one,
        # and remove leading * (prefix-only needs at least one char before *)
        sanitized = re.sub(r"\*+", "*", sanitized)
        sanitized = re.sub(r"(^|\s)\*", r"\1", sanitized)

        # Step 4: Remove dangling boolean operators at start/end that would
        # cause syntax errors (e.g. "hello AND" or "OR world")
        sanitized = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", sanitized.strip())
        sanitized = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", sanitized.strip())

        # Step 5: Wrap unquoted dotted and/or hyphenated terms in double
        # quotes.  FTS5's tokenizer splits on dots and hyphens, turning
        # ``chat-send`` into ``chat AND send`` and ``P2.2`` into ``p2 AND 2``.
        # Quoting preserves phrase semantics.  A single pass avoids the
        # double-quoting bug that would occur if dotted and hyphenated
        # patterns were applied sequentially (e.g. ``my-app.config``).
        sanitized = re.sub(r"\b(\w+(?:[.-]\w+)+)\b", r'"\1"', sanitized)

        # Step 6: Restore preserved quoted phrases
        for i, quoted in enumerate(_quoted_parts):
            sanitized = sanitized.replace(f"\x00Q{i}\x00", quoted)

        return sanitized.strip()

    def search_messages(
        self,
        query: str,
        source_filter: List[str] = None,
        exclude_sources: List[str] = None,
        role_filter: List[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Full-text search across session messages using FTS5.

        Supports FTS5 query syntax:
          - Simple keywords: "docker deployment"
          - Phrases: '"exact phrase"'
          - Boolean: "docker OR kubernetes", "python NOT java"
          - Prefix: "deploy*"

        Returns matching messages with session metadata, content snippet,
        and surrounding context (1 message before and after the match).
        """
        if not query or not query.strip():
            return []

        query = self._sanitize_fts5_query(query)
        if not query:
            return []

        # Build WHERE clauses dynamically
        where_clauses = ["messages_fts MATCH ?"]
        params: list = [query]

        if source_filter is not None:
            source_placeholders = ",".join("?" for _ in source_filter)
            where_clauses.append(f"s.source IN ({source_placeholders})")
            params.extend(source_filter)

        if exclude_sources is not None:
            exclude_placeholders = ",".join("?" for _ in exclude_sources)
            where_clauses.append(f"s.source NOT IN ({exclude_placeholders})")
            params.extend(exclude_sources)

        if role_filter:
            role_placeholders = ",".join("?" for _ in role_filter)
            where_clauses.append(f"m.role IN ({role_placeholders})")
            params.extend(role_filter)

        where_sql = " AND ".join(where_clauses)
        params.extend([limit, offset])

        sql = f"""
            SELECT
                m.id,
                m.session_id,
                m.role,
                snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS snippet,
                m.content,
                m.timestamp,
                m.tool_name,
                s.source,
                s.model,
                s.started_at AS session_started
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.id = m.session_id
            WHERE {where_sql}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """

        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
            except sqlite3.OperationalError:
                # FTS5 query syntax error despite sanitization — return empty
                return []
            matches = [dict(row) for row in cursor.fetchall()]

        # Add surrounding context (1 message before + after each match).
        # Done outside the lock so we don't hold it across N sequential queries.
        for match in matches:
            try:
                with self._lock:
                    ctx_cursor = self._conn.execute(
                        """SELECT role, content FROM messages
                           WHERE session_id = ? AND id >= ? - 1 AND id <= ? + 1
                           ORDER BY id""",
                        (match["session_id"], match["id"], match["id"]),
                    )
                    context_msgs = [
                        {"role": r["role"], "content": (r["content"] or "")[:200]}
                        for r in ctx_cursor.fetchall()
                    ]
                match["context"] = context_msgs
            except Exception:
                match["context"] = []

        # Remove full content from result (snippet is enough, saves tokens)
        for match in matches:
            match.pop("content", None)

        return matches

    def search_sessions(
        self,
        source: str = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered by source."""
        with self._lock:
            if source:
                cursor = self._conn.execute(
                    "SELECT * FROM sessions WHERE source = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
                    (source, limit, offset),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Utility
    # =========================================================================

    def session_count(self, source: str = None) -> int:
        """Count sessions, optionally filtered by source."""
        with self._lock:
            if source:
                cursor = self._conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE source = ?", (source,)
                )
            else:
                cursor = self._conn.execute("SELECT COUNT(*) FROM sessions")
            return cursor.fetchone()[0]

    def message_count(self, session_id: str = None) -> int:
        """Count messages, optionally for a specific session."""
        with self._lock:
            if session_id:
                cursor = self._conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
                )
            else:
                cursor = self._conn.execute("SELECT COUNT(*) FROM messages")
            return cursor.fetchone()[0]

    # =========================================================================
    # Export and cleanup
    # =========================================================================

    def export_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Export a single session with all its messages as a dict."""
        session = self.get_session(session_id)
        if not session:
            return None
        messages = self.get_messages(session_id)
        return {**session, "messages": messages}

    def export_all(self, source: str = None) -> List[Dict[str, Any]]:
        """
        Export all sessions (with messages) as a list of dicts.
        Suitable for writing to a JSONL file for backup/analysis.
        """
        sessions = self.search_sessions(source=source, limit=100000)
        results = []
        for session in sessions:
            messages = self.get_messages(session["id"])
            results.append({**session, "messages": messages})
        return results

    def clear_messages(self, session_id: str) -> None:
        """Delete all messages for a session and reset its counters."""
        def _do(conn):
            conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            conn.execute(
                "UPDATE sessions SET message_count = 0, tool_call_count = 0 WHERE id = ?",
                (session_id,),
            )
        self._execute_write(_do)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its messages. Returns True if found."""
        def _do(conn):
            cursor = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE id = ?", (session_id,)
            )
            if cursor.fetchone()[0] == 0:
                return False
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return True
        return self._execute_write(_do)

    def prune_sessions(self, older_than_days: int = 90, source: str = None) -> int:
        """
        Delete sessions older than N days. Returns count of deleted sessions.
        Only prunes ended sessions (not active ones).
        """
        cutoff = time.time() - (older_than_days * 86400)

        def _do(conn):
            if source:
                cursor = conn.execute(
                    """SELECT id FROM sessions
                       WHERE started_at < ? AND ended_at IS NOT NULL AND source = ?""",
                    (cutoff, source),
                )
            else:
                cursor = conn.execute(
                    "SELECT id FROM sessions WHERE started_at < ? AND ended_at IS NOT NULL",
                    (cutoff,),
                )
            session_ids = [row["id"] for row in cursor.fetchall()]

            for sid in session_ids:
                conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            return len(session_ids)

        return self._execute_write(_do)
