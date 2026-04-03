"""Tests for the hot-path autonomy watch tool."""

import json

from hermes_state import SessionDB
from tools.autonomy_watch_tool import autonomy_watch


class TestAutonomyWatchTool:
    def test_upsert_list_and_resolve(self, tmp_path, monkeypatch):
        db = SessionDB(db_path=tmp_path / "state.db")
        monkeypatch.setenv("HERMES_AUTONOMY_ENABLED", "1")
        monkeypatch.setenv("HERMES_SESSION_KEY", "telegram:chat:123")
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
        monkeypatch.setenv("HERMES_AUTONOMY_INTERVAL_SECONDS", "600")

        created = json.loads(
            autonomy_watch(
                "upsert",
                title="OpenAI news",
                kind="topic",
                description="Keep an eye on notable OpenAI updates.",
                session_db=db,
            )
        )
        assert created["success"] is True
        assert created["watch_item"]["title"] == "OpenAI news"

        listed = json.loads(autonomy_watch("list", session_db=db))
        assert listed["success"] is True
        assert listed["count"] == 1
        assert listed["watch_items"][0]["title"] == "OpenAI news"

        resolved = json.loads(
            autonomy_watch(
                "resolve",
                title="OpenAI news",
                session_db=db,
            )
        )
        assert resolved["success"] is True
        assert resolved["resolved_keys"]

        listed_after = json.loads(autonomy_watch("list", session_db=db))
        assert listed_after["count"] == 0
        db.close()

    def test_requires_session_db(self, monkeypatch):
        monkeypatch.setenv("HERMES_AUTONOMY_ENABLED", "1")
        monkeypatch.setenv("HERMES_SESSION_KEY", "telegram:chat:123")

        result = json.loads(autonomy_watch("list", session_db=None))

        assert result["success"] is False
