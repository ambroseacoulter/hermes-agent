"""Hot-path autonomy watch management for gateway sessions."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from gateway.autonomy import (
    normalize_resolved_watch_keys,
    normalize_watch_items,
)
from tools.registry import registry


def check_autonomy_watch_requirements() -> bool:
    """Available only for gateway sessions with autonomy enabled."""
    extract_behavior = (
        str(os.getenv("HERMES_AUTONOMY_EXTRACT_BEHAVIOR", "both") or "both")
        .strip()
        .lower()
    )
    return bool(
        os.getenv("HERMES_AUTONOMY_ENABLED") == "1"
        and os.getenv("HERMES_SESSION_KEY")
        and extract_behavior in {"hermes", "both"}
    )


def _next_check_at() -> float:
    try:
        interval = int(os.getenv("HERMES_AUTONOMY_INTERVAL_SECONDS", "0") or "0")
    except ValueError:
        interval = 0
    return time.time() + max(60, min(max(interval, 0), 1800))


def _watch_payload(
    *,
    key: Optional[str],
    title: str,
    kind: Optional[str],
    description: Optional[str],
    importance: Optional[str],
) -> Dict[str, Any]:
    item: Dict[str, Any] = {"title": title}
    if key:
        item["key"] = key
    if kind:
        item["kind"] = kind
    if description:
        item["description"] = description
    if importance:
        item["importance"] = importance
    return {"watch_items": [item]}


def autonomy_watch(
    action: str,
    *,
    title: Optional[str] = None,
    key: Optional[str] = None,
    kind: Optional[str] = None,
    description: Optional[str] = None,
    importance: Optional[str] = None,
    limit: int = 20,
    session_db=None,
    task_id: str = None,
) -> str:
    del task_id
    if session_db is None:
        return json.dumps({"success": False, "error": "autonomy watch state is unavailable"}, indent=2)

    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"upsert", "resolve", "list"}:
        return json.dumps({"success": False, "error": "action must be one of: upsert, resolve, list"}, indent=2)

    if normalized_action == "list":
        items = session_db.list_autonomy_watch_items(statuses=["active", "paused"], limit=max(1, int(limit)))
        return json.dumps(
            {
                "success": True,
                "count": len(items),
                "watch_items": items,
            },
            indent=2,
        )

    existing_items = session_db.list_autonomy_watch_items(
        statuses=["active", "paused", "resolved"],
        limit=100,
    )

    if normalized_action == "resolve":
        target = str(key or title or "").strip()
        if not target:
            return json.dumps({"success": False, "error": "resolve requires key or title"}, indent=2)
        resolved_keys = normalize_resolved_watch_keys(
            {"resolved_watch_keys": [target]},
            existing_items=existing_items,
        )
        if not resolved_keys and key:
            resolved_keys = [key]
        if not resolved_keys:
            return json.dumps({"success": False, "error": f"No matching autonomy watch found for '{target}'"}, indent=2)
        for resolved_key in resolved_keys:
            session_db.update_autonomy_watch_item(
                resolved_key,
                status="resolved",
                next_check_at=_next_check_at(),
                last_checked_at=time.time(),
            )
        return json.dumps(
            {
                "success": True,
                "resolved_keys": resolved_keys,
                "message": f"Resolved {len(resolved_keys)} autonomy watch item(s).",
            },
            indent=2,
        )

    if not str(title or "").strip():
        return json.dumps({"success": False, "error": "upsert requires title"}, indent=2)

    items = normalize_watch_items(
        _watch_payload(
            key=key,
            title=str(title or "").strip(),
            kind=kind,
            description=description,
            importance=importance,
        ),
        "implied",
        existing_items=existing_items,
    )
    if not items:
        return json.dumps({"success": False, "error": "could not normalize autonomy watch item"}, indent=2)

    item = items[0]
    record = session_db.upsert_autonomy_watch_item(
        normalized_key=item["normalized_key"],
        title=item["title"],
        kind=item["kind"],
        description=item["description"],
        importance=item["importance"],
        source_session_key=os.getenv("HERMES_SESSION_KEY"),
        source_message_ref=None,
        inference_mode=item["inference_mode"],
        due_at=item["due_at"].timestamp() if item["due_at"] else None,
        next_check_at=_next_check_at(),
        metadata={"source_platform": os.getenv("HERMES_SESSION_PLATFORM", "")},
        status="active",
    )
    return json.dumps(
        {
            "success": True,
            "watch_item": {
                "normalized_key": item["normalized_key"],
                "title": item["title"],
                "kind": item["kind"],
                "description": item["description"],
                "importance": item["importance"],
            },
            "changed": bool(record.get("changed")),
            "message": "Autonomy watch registered.",
        },
        indent=2,
    )


AUTONOMY_WATCH_SCHEMA = {
    "name": "autonomy_watch",
    "description": (
        "Manage profile-scoped autonomy watch items for open-ended monitoring. "
        "Use this instead of cron for requests like 'keep an eye on X' or 'let me know if anything interesting happens' "
        "when the user did not ask for a specific schedule. Supports registering a watch, resolving a watch, or listing active watches."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "One of: upsert, resolve, list",
            },
            "title": {
                "type": "string",
                "description": "For upsert: the subject to watch. For resolve: optional title to match when key is unknown.",
            },
            "key": {
                "type": "string",
                "description": "Optional explicit watch key for updating or resolving an existing watch.",
            },
            "kind": {
                "type": "string",
                "description": "Optional watch kind such as topic, repo, issue, person, deadline, project, or other.",
            },
            "description": {
                "type": "string",
                "description": "Optional short note about what Hermes should keep an eye on.",
            },
            "importance": {
                "type": "string",
                "description": "Optional importance: low, normal, high, or critical.",
            },
            "limit": {
                "type": "integer",
                "description": "For list: maximum number of active watch items to return.",
            },
        },
        "required": ["action"],
    },
}


registry.register(
    name="autonomy_watch",
    toolset="autonomy",
    schema=AUTONOMY_WATCH_SCHEMA,
    handler=lambda args, **kw: autonomy_watch(
        action=args.get("action", ""),
        title=args.get("title"),
        key=args.get("key"),
        kind=args.get("kind"),
        description=args.get("description"),
        importance=args.get("importance"),
        limit=args.get("limit", 20),
        session_db=kw.get("session_db"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_autonomy_watch_requirements,
    emoji="🛰️",
)
