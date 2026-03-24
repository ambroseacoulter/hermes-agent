"""Gateway Cortex signal tests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig, StreamingConfig
from gateway.platforms.base import SendResult
from gateway.session import SessionSource


def _make_runner(tmp_path):
    from gateway.run import GatewayRunner

    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token"),
            Platform.WEBHOOK: PlatformConfig(enabled=True, extra={}),
        },
        sessions_dir=tmp_path / "sessions",
        streaming=StreamingConfig(enabled=False),
        always_log_local=False,
    )
    runner = GatewayRunner(config=config)
    return runner


class FakeAIAgent:
    instances = []
    emit_signal_payload = None

    def __init__(self, *args, **kwargs):
        self.__class__.instances.append(self)
        self.tools = []
        self.enabled_toolsets = kwargs.get("enabled_toolsets") or []
        self.signal_callback = kwargs.get("signal_callback")
        self.turn_signal_poll_callback = kwargs.get("turn_signal_poll_callback")
        self.model = kwargs.get("model", "fake-model")
        self.provider = kwargs.get("provider", "openrouter")
        self.base_url = kwargs.get("base_url", "https://example.test/v1")
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0)
        self.session_id = kwargs.get("session_id", "sess_fake")
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0

    def run_conversation(self, message, conversation_history=None, task_id=None, persist_user_message=None):
        self.message = message
        self.persist_user_message = persist_user_message
        if self.emit_signal_payload and self.signal_callback:
            self.signal_callback(**self.emit_signal_payload)
        return {
            "final_response": "ok",
            "messages": [
                {"role": "user", "content": persist_user_message or message},
                {"role": "assistant", "content": "ok"},
            ],
            "api_calls": 1,
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "last_prompt_tokens": 0,
            "estimated_cost_usd": 0.0,
            "cost_status": "included",
            "cost_source": "test",
        }


@pytest.mark.asyncio
async def test_run_agent_adds_cortex_signal_toolset_only_for_signal_webhooks(tmp_path, monkeypatch):
    from gateway.run import GatewayRunner

    runner = _make_runner(tmp_path)
    source = SessionSource(
        platform=Platform.WEBHOOK,
        chat_id="webhook:email:1",
        chat_type="webhook",
        user_id="webhook:email",
    )

    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda: "fake-model")
    monkeypatch.setattr(
        "gateway.run._resolve_runtime_agent_kwargs",
        lambda: {
            "api_key": "test-key",
            "base_url": "https://example.test/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
        },
    )
    monkeypatch.setattr(
        GatewayRunner,
        "_resolve_turn_agent_config",
        lambda self, user_message, model, runtime_kwargs: {"model": model, "runtime": runtime_kwargs},
    )
    monkeypatch.setattr(GatewayRunner, "_get_or_create_gateway_honcho", lambda self, session_key: (None, None))
    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)

    FakeAIAgent.instances.clear()
    await runner._run_agent(
        message="process webhook",
        context_prompt="ctx",
        history=[],
        source=source,
        session_id="sess_webhook",
        session_key="agent:main:webhook:webhook:webhook:email:1",
        event_metadata={
            "webhook": {
                "route_name": "email",
                "delivery_id": "1",
                "deliver_type": "signal",
                "deliver": "telegram",
                "deliver_extra": {"chat_id": "tg-123"},
            }
        },
    )
    assert FakeAIAgent.instances
    assert "cortex-signal" in FakeAIAgent.instances[-1].enabled_toolsets
    assert FakeAIAgent.instances[-1].signal_callback is not None
    assert FakeAIAgent.instances[-1].turn_signal_poll_callback is None

    FakeAIAgent.instances.clear()
    await runner._run_agent(
        message="process webhook",
        context_prompt="ctx",
        history=[],
        source=source,
        session_id="sess_webhook",
        session_key="agent:main:webhook:webhook:webhook:email:1",
        event_metadata={
            "webhook": {
                "route_name": "email",
                "delivery_id": "1",
                "deliver_type": "always",
                "deliver": "telegram",
                "deliver_extra": {"chat_id": "tg-123"},
            }
        },
    )
    assert FakeAIAgent.instances
    assert "cortex-signal" not in FakeAIAgent.instances[-1].enabled_toolsets
    assert FakeAIAgent.instances[-1].signal_callback is None
    assert FakeAIAgent.instances[-1].turn_signal_poll_callback is None


@pytest.mark.asyncio
async def test_signal_mode_webhook_run_can_enqueue_cortex_signal(tmp_path, monkeypatch):
    from gateway.run import GatewayRunner

    runner = _make_runner(tmp_path)
    target_source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="tg-123",
        chat_type="dm",
        user_id="user-telegram",
    )
    target_entry = runner.session_store.get_or_create_session(target_source)

    source = SessionSource(
        platform=Platform.WEBHOOK,
        chat_id="webhook:email:2",
        chat_type="webhook",
        user_id="webhook:email",
    )

    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda: "fake-model")
    monkeypatch.setattr(
        "gateway.run._resolve_runtime_agent_kwargs",
        lambda: {
            "api_key": "test-key",
            "base_url": "https://example.test/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
        },
    )
    monkeypatch.setattr(
        GatewayRunner,
        "_resolve_turn_agent_config",
        lambda self, user_message, model, runtime_kwargs: {"model": model, "runtime": runtime_kwargs},
    )
    monkeypatch.setattr(GatewayRunner, "_get_or_create_gateway_honcho", lambda self, session_key: (None, None))
    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)

    FakeAIAgent.instances.clear()
    FakeAIAgent.emit_signal_payload = {
        "title": "Invoice needs review",
        "summary": "A draft reply was prepared for the invoice email.",
        "reason": "input_required",
        "priority": "normal",
        "action_items": ["Review the draft", "Confirm whether to send it"],
        "metadata": {"email_id": "email_88", "draft_id": "draft_99"},
    }
    runner._running_agents[target_entry.session_key] = object()
    try:
        await runner._run_agent(
            message="process webhook",
            context_prompt="ctx",
            history=[],
            source=source,
            session_id="sess_webhook",
            session_key="agent:main:webhook:webhook:webhook:email:2",
            event_metadata={
                "webhook": {
                    "route_name": "email",
                    "delivery_id": "2",
                    "deliver_type": "signal",
                    "deliver": "telegram",
                    "deliver_extra": {"chat_id": "tg-123"},
                }
            },
        )
    finally:
        FakeAIAgent.emit_signal_payload = None
        runner._running_agents.pop(target_entry.session_key, None)

    assert runner.cortex.has_pending(target_entry.session_key)
    signals = runner.cortex.claim_pending(target_entry.session_key)
    assert len(signals) == 1
    assert signals[0].title == "Invoice needs review"
    assert signals[0].reason == "input_required"
    assert signals[0].metadata["email_id"] == "email_88"


@pytest.mark.asyncio
async def test_deliver_pending_signals_wakes_session_naturally(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    adapter = AsyncMock()
    adapter.send = AsyncMock(return_value=SendResult(success=True))
    runner.adapters = {Platform.TELEGRAM: adapter}

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="user-1",
        user_name="Ambrose",
    )
    entry = runner.session_store.get_or_create_session(source)
    session_key = entry.session_key

    captured = {}

    async def fake_handle_message_with_agent(event, source_arg, quick_key):
        captured["event"] = event
        captured["source"] = source_arg
        captured["quick_key"] = quick_key
        return "Here is the follow-up."

    monkeypatch.setattr(runner, "_handle_message_with_agent", fake_handle_message_with_agent)

    runner.cortex.create_signal(
        target_session_key=session_key,
        source_type="webhook",
        source_ref="email:delivery-1",
        title="Electric bill due",
        summary="The electricity invoice is due next week and a draft reply was prepared.",
        reason="notify",
        action_items=["Review the draft reply", "Confirm whether to pay this week"],
        metadata={"email_id": "email_123", "draft_id": "draft_456"},
    )

    await runner._deliver_pending_signals(session_key)

    assert not runner.cortex.has_pending(session_key)
    assert captured["quick_key"] == session_key
    assert captured["event"].metadata["persist_user_message"] == '[System event: signal "Electric bill due" received.]'
    assert "email_123" in captured["event"].text
    assert "Do not mention internal signal or webhook machinery" in captured["event"].text
    adapter.send.assert_awaited_once_with("12345", "Here is the follow-up.", metadata=None)


@pytest.mark.asyncio
async def test_run_agent_schedules_post_turn_signal_followup_after_cleanup(tmp_path, monkeypatch):
    from gateway.run import GatewayRunner

    runner = _make_runner(tmp_path)
    source = SessionSource(
        platform=Platform.WEBHOOK,
        chat_id="webhook:email:postturn",
        chat_type="webhook",
        user_id="webhook:email",
    )
    session_key = "agent:main:webhook:webhook:webhook:email:postturn"

    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda: "fake-model")
    monkeypatch.setattr(
        "gateway.run._resolve_runtime_agent_kwargs",
        lambda: {
            "api_key": "test-key",
            "base_url": "https://example.test/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
        },
    )
    monkeypatch.setattr(
        GatewayRunner,
        "_resolve_turn_agent_config",
        lambda self, user_message, model, runtime_kwargs: {"model": model, "runtime": runtime_kwargs},
    )
    monkeypatch.setattr(GatewayRunner, "_get_or_create_gateway_honcho", lambda self, session_key: (None, None))
    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)

    runner.cortex.create_signal(
        target_session_key=session_key,
        source_type="webhook",
        source_ref="email:postturn",
        title="Queued during run",
        summary="This signal should be delivered after the foreground run ends.",
    )
    deliver_mock = AsyncMock()
    monkeypatch.setattr(runner, "_deliver_pending_signals", deliver_mock)

    await runner._run_agent(
        message="process webhook",
        context_prompt="ctx",
        history=[],
        source=source,
        session_id="sess_webhook",
        session_key=session_key,
        event_metadata={
            "webhook": {
                "route_name": "email",
                "delivery_id": "postturn",
                "deliver_type": "signal",
                "deliver": "telegram",
                "deliver_extra": {"chat_id": "tg-123"},
            }
        },
    )

    await asyncio.sleep(0.5)
    deliver_mock.assert_awaited_once_with(session_key)
