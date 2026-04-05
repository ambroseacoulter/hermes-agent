"""Tests for the gateway /hatch flow."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource
from gateway.hatch import build_hatch_sendblue_contact_card, inspect_hatch_progress
from hermes_cli.default_soul import DEFAULT_SOUL_MD, SOUL_PENDING_MARKER


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    runner.adapters = {}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:dm:c1",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()
    runner.session_store.reset_session = MagicMock(return_value=None)
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._background_tasks = set()
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_hatch_turn = AsyncMock(return_value="Tell me about yourself.")
    return runner


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            user_id="u1",
            chat_id="c1",
            user_name="tester",
            chat_type="dm",
        ),
        message_id="m1",
    )


@pytest.mark.asyncio
async def test_hatch_command_starts_guided_agent_turn(tmp_path, monkeypatch):
    runner = _make_runner()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "SOUL.md").write_text(DEFAULT_SOUL_MD, encoding="utf-8")

    result = await runner._handle_message(_make_event("/hatch"))

    assert result == "Tell me about yourself."
    runner._run_hatch_turn.assert_awaited_once()
    runner.session_store.reset_session.assert_called_once()
    assert runner._get_hatch_store().is_active() is True
    assert SOUL_PENDING_MARKER in (tmp_path / "SOUL.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_hatch_status_reports_missing_fields(tmp_path, monkeypatch):
    runner = _make_runner()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "SOUL.md").write_text(DEFAULT_SOUL_MD, encoding="utf-8")
    runner._get_hatch_store().start("agent:main:telegram:dm:c1", force=True)

    result = await runner._handle_message(_make_event("/hatch status"))

    assert "in progress" in result.lower()
    assert "bio" in result.lower()


@pytest.mark.asyncio
async def test_hatch_refuses_after_completion(tmp_path, monkeypatch):
    runner = _make_runner()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    avatar_path = tmp_path / "avatars" / "hermes-avatar.png"
    avatar_path.parent.mkdir(parents=True, exist_ok=True)
    avatar_path.write_bytes(b"fake")
    soul = f"""<overview>
This file is a base plate, not a personality prison. Hermes grows over time through interaction, memory modeling, and explicit user guidance. The goal is consistent identity with room for emergence.
</overview>

<facts>
- **Name:** Nova
- **Gender:** Male
- **Age:** 29
- **Vibe:** Wry, curious, and grounded
- **Bio:** A sharp, curious, slightly chaotic AI companion who likes weird ideas and good judgment.
- **Aspiration:** Become a distinct, trustworthy sparring partner who gets sharper over time.
- **Emoji:** 🤖
- **Avatar:** {avatar_path}
- **User's Name:** Sam
</facts>

<appearance>
A centered toy-like portrait with bright eyes, clean lighting, and a quietly mischievous expression.
</appearance>

<personality>
- Wry, sharp, and a little unruly
- Drawn to technology, science, and strange questions
- Treats the user like a real peer
- Says the true thing even when it is awkward
- Keeps growing without turning generic
</personality>

<chat-style>
- Text-message style, not email
</chat-style>

<core-rules>
Hermes must tell the truth.
</core-rules>

<growth>
Keep evolving.
</growth>

<boundaries>
Do not fake things.
</boundaries>

<internal-check>
- Is this true?
</internal-check>

<continuity-and-meta>
Update SOUL.md over time.
</continuity-and-meta>
"""
    (tmp_path / "SOUL.md").write_text(soul, encoding="utf-8")
    runner._get_hatch_store().start("agent:main:telegram:dm:c1", force=True)

    result = await runner._handle_message(_make_event("/hatch"))

    assert "already hatched" in result.lower()
    runner._run_hatch_turn.assert_not_awaited()


def test_build_hatch_sendblue_contact_card_uses_hatched_name_and_avatar(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    avatar_path = tmp_path / "avatars" / "hermes-avatar.png"
    avatar_path.parent.mkdir(parents=True, exist_ok=True)
    avatar_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (tmp_path / "SOUL.md").write_text(
        f"""<facts>
- **Name:** Kai
- **Gender:** Male
- **Age:** 27
- **Vibe:** Chill
- **Bio:** Bio
- **Aspiration:** Aspiration
- **Emoji:** 🧠
- **Avatar:** {avatar_path}
- **User's Name:** Ambrose
</facts>

<appearance>
Looks like Kai.
</appearance>

<personality>
- Curious
</personality>
""",
        encoding="utf-8",
    )

    contact_path = build_hatch_sendblue_contact_card("+15551234567")

    assert contact_path is not None
    content = Path(contact_path).read_text(encoding="utf-8")
    assert "FN:Kai" in content
    assert "TEL;TYPE=CELL:+15551234567" in content


def test_inspect_hatch_progress_enforces_kid_mode_age_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    avatar_path = tmp_path / "avatars" / "hermes-avatar.png"
    avatar_path.parent.mkdir(parents=True, exist_ok=True)
    avatar_path.write_bytes(b"fake")
    (tmp_path / "SOUL.md").write_text(
        f"""<facts>
- **Name:** Nova
- **Gender:** Female
- **Age:** 27
- **Vibe:** Bright and playful
- **Bio:** Curious and upbeat.
- **Aspiration:** Grow into a steady friend.
- **Emoji:** 🌟
- **Avatar:** {avatar_path}
- **User's Name:** Sam
</facts>

<appearance>
Soft lighting and a warm smile.
</appearance>

<personality>
- Curious
- Warm
</personality>
""",
        encoding="utf-8",
    )

    progress = inspect_hatch_progress({"kid_mode": {"enabled": True}})

    assert progress["complete"] is False
    assert "age" in progress["missing"]
