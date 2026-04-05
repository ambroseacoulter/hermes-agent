"""Tests for hatch-mode system prompt integration in AIAgent."""

from unittest.mock import patch

from run_agent import AIAgent


def test_build_system_prompt_includes_hatch_mode_guidance():
    agent = object.__new__(AIAgent)
    agent.skip_context_files = True
    agent.valid_tool_names = set()
    agent._tool_use_enforcement = False
    agent._kid_mode_guidance = ""
    agent._hatch_mode_guidance = "Hatch guidance block"
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

    assert "Hatch guidance block" in prompt


def test_build_system_prompt_skips_duplicate_soul_context_without_name_error():
    agent = object.__new__(AIAgent)
    agent.skip_context_files = False
    agent.valid_tool_names = set()
    agent._tool_use_enforcement = False
    agent._kid_mode_guidance = ""
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

    with (
        patch("run_agent.load_soul_md", return_value="<facts>\n- **Name:** Nova\n</facts>"),
        patch("run_agent.build_context_files_prompt", return_value="Project context block") as mock_context,
    ):
        prompt = agent._build_system_prompt()

    assert "<facts>" in prompt
    assert "Project context block" in prompt
    assert mock_context.call_args.kwargs["skip_soul"] is True
