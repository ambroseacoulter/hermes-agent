"""Tests for guarding autonomy intake against synthetic/internal turns."""

from gateway.config import AutonomyConfig, GatewayConfig
from gateway.platforms.base import MessageEvent
from gateway.config import Platform
from gateway.session import SessionSource


def _source():
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        user_id="user-1",
        user_name="tester",
    )


def test_default_events_are_intake_eligible():
    from gateway.run import GatewayRunner

    event = MessageEvent(text="Keep an eye on OpenAI for me.", source=_source())

    assert GatewayRunner._should_schedule_autonomy_intake(event) is True


def test_synthetic_events_can_skip_autonomy_intake():
    from gateway.run import GatewayRunner

    event = MessageEvent(
        text="[System continuation]",
        source=_source(),
        skip_autonomy_intake=True,
    )

    assert GatewayRunner._should_schedule_autonomy_intake(event) is False


def test_post_turn_intake_skips_when_mode_is_hermes():
    from gateway.run import GatewayRunner

    runner = GatewayRunner(config=GatewayConfig(autonomy=AutonomyConfig(extract_behavior="hermes")))

    assert runner._should_run_post_turn_autonomy_intake([]) is False


def test_post_turn_intake_runs_in_auto_extract_mode():
    from gateway.run import GatewayRunner

    runner = GatewayRunner(config=GatewayConfig(autonomy=AutonomyConfig(extract_behavior="auto_extract")))

    assert runner._should_run_post_turn_autonomy_intake([]) is True


def test_post_turn_intake_skips_when_autonomy_watch_was_used():
    from gateway.run import GatewayRunner

    runner = GatewayRunner(config=GatewayConfig(autonomy=AutonomyConfig(extract_behavior="both")))
    turn_messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "call-1", "function": {"name": "autonomy_watch"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "{\"success\": true}"},
    ]

    assert runner._should_run_post_turn_autonomy_intake(turn_messages) is False


def test_post_turn_intake_falls_back_when_autonomy_watch_failed():
    from gateway.run import GatewayRunner

    runner = GatewayRunner(config=GatewayConfig(autonomy=AutonomyConfig(extract_behavior="both")))
    turn_messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "call-2", "function": {"name": "autonomy_watch"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-2", "content": "{\"success\": false, \"error\": \"state unavailable\"}"},
    ]

    assert runner._should_run_post_turn_autonomy_intake(turn_messages) is True


def test_supervisor_summary_collapses_noop_runs():
    from gateway.run import GatewayRunner

    summary = GatewayRunner._summarize_autonomy_supervisor_run(
        payload_summary="OpenAI watch is live and everything is normal.",
        created_findings=0,
        created_artifacts=0,
        meaningful_watch_updates=0,
    )

    assert summary == "No new findings."


def test_supervisor_summary_preserves_changes():
    from gateway.run import GatewayRunner

    summary = GatewayRunner._summarize_autonomy_supervisor_run(
        payload_summary="Resolved duplicate watch.",
        created_findings=0,
        created_artifacts=0,
        meaningful_watch_updates=1,
    )

    assert summary == "Resolved duplicate watch."
