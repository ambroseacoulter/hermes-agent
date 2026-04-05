"""Tests for kid-mode system prompt integration in AIAgent."""

from run_agent import AIAgent


def test_build_system_prompt_includes_kid_mode_guidance():
    agent = object.__new__(AIAgent)
    agent.skip_context_files = True
    agent.valid_tool_names = set()
    agent._tool_use_enforcement = False
    agent._kid_mode_guidance = "Kid guidance block"
    agent._hatch_mode_guidance = ""
    agent.model = "test/model"
    agent.provider = "test-provider"
    agent._memory_store = None
    agent._memory_enabled = False
    agent._user_profile_enabled = False
    agent._memory_manager = None
    agent.platform = None
    agent.pass_session_id = False
    agent.session_id = "sess-1"

    prompt = agent._build_system_prompt()

    assert "Kid guidance block" in prompt
