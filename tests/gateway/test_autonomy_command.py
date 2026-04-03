"""Tests for the /autonomy gateway command."""

from unittest.mock import MagicMock

import pytest

from gateway.config import AutonomyConfig, GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/autonomy"):
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="67890",
        user_id="12345",
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _make_runner(session_db):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._session_db = session_db
    runner.config = GatewayConfig(
        autonomy=AutonomyConfig(
            enabled=True,
            home_platform="telegram",
            home_chat_id="67890",
        )
    )
    runner.session_store = MagicMock()
    return runner


class TestAutonomyCommand:
    @pytest.mark.asyncio
    async def test_status_reports_counts_and_home(self, tmp_path):
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        runner = _make_runner(db)

        result = await runner._handle_autonomy_command(_make_event("/autonomy"))

        assert "Autonomy status:" in result
        assert "Enabled: yes" in result
        assert "Home: telegram:67890" in result
        db.close()

    @pytest.mark.asyncio
    async def test_pause_and_resume_toggle_state(self, tmp_path):
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        runner = _make_runner(db)

        paused = await runner._handle_autonomy_command(_make_event("/autonomy pause"))
        resumed = await runner._handle_autonomy_command(_make_event("/autonomy resume"))

        assert paused == "Autonomy paused for this profile."
        assert resumed == "Autonomy resumed for this profile."
        assert db.get_autonomy_state()["paused"] == 0
        db.close()

    @pytest.mark.asyncio
    async def test_inbox_lists_items(self, tmp_path):
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        db.upsert_autonomy_inbox_item(
            source_type="finding",
            source_id=1,
            title="Watch launch",
            message_preview="Launch moved by one week.",
            importance="high",
        )
        runner = _make_runner(db)

        result = await runner._handle_autonomy_command(_make_event("/autonomy inbox"))

        assert "Autonomy inbox:" in result
        assert "Watch launch" in result
        db.close()
