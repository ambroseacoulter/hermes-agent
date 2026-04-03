"""Tests for model_tools.py — function call dispatch, agent-loop interception, legacy toolsets."""

import json
import pytest

from model_tools import (
    handle_function_call,
    get_all_tool_names,
    get_tool_definitions,
    get_toolset_for_tool,
    _AGENT_LOOP_TOOLS,
    _LEGACY_TOOLSET_MAP,
    TOOL_TO_TOOLSET_MAP,
)


# =========================================================================
# handle_function_call
# =========================================================================

class TestHandleFunctionCall:
    def test_agent_loop_tool_returns_error(self):
        for tool_name in _AGENT_LOOP_TOOLS:
            result = json.loads(handle_function_call(tool_name, {}))
            assert "error" in result
            assert "agent loop" in result["error"].lower()

    def test_unknown_tool_returns_error(self):
        result = json.loads(handle_function_call("totally_fake_tool_xyz", {}))
        assert "error" in result
        assert "totally_fake_tool_xyz" in result["error"]

    def test_exception_returns_json_error(self):
        # Even if something goes wrong, should return valid JSON
        result = handle_function_call("web_search", None)  # None args may cause issues
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "error" in parsed
        assert len(parsed["error"]) > 0
        assert "error" in parsed["error"].lower() or "failed" in parsed["error"].lower()


# =========================================================================
# Agent loop tools
# =========================================================================

class TestAgentLoopTools:
    def test_expected_tools_in_set(self):
        assert "todo" in _AGENT_LOOP_TOOLS
        assert "memory" in _AGENT_LOOP_TOOLS
        assert "session_search" in _AGENT_LOOP_TOOLS
        assert "delegate_task" in _AGENT_LOOP_TOOLS

    def test_no_regular_tools_in_set(self):
        assert "web_search" not in _AGENT_LOOP_TOOLS
        assert "terminal" not in _AGENT_LOOP_TOOLS


# =========================================================================
# Legacy toolset map
# =========================================================================

class TestLegacyToolsetMap:
    def test_expected_legacy_names(self):
        expected = [
            "web_tools", "terminal_tools", "vision_tools", "moa_tools",
            "image_tools", "skills_tools", "browser_tools", "cronjob_tools",
            "rl_tools", "file_tools", "tts_tools",
        ]
        for name in expected:
            assert name in _LEGACY_TOOLSET_MAP, f"Missing legacy toolset: {name}"

    def test_values_are_lists_of_strings(self):
        for name, tools in _LEGACY_TOOLSET_MAP.items():
            assert isinstance(tools, list), f"{name} is not a list"
            for tool in tools:
                assert isinstance(tool, str), f"{name} contains non-string: {tool}"


# =========================================================================
# Backward-compat wrappers
# =========================================================================

class TestBackwardCompat:
    def test_get_all_tool_names_returns_list(self):
        names = get_all_tool_names()
        assert isinstance(names, list)
        assert len(names) > 0
        # Should contain well-known tools
        assert "web_search" in names
        assert "terminal" in names

    def test_get_toolset_for_tool(self):
        result = get_toolset_for_tool("web_search")
        assert result is not None
        assert isinstance(result, str)

    def test_get_toolset_for_unknown_tool(self):
        result = get_toolset_for_tool("totally_nonexistent_tool")
        assert result is None

    def test_tool_to_toolset_map(self):
        assert isinstance(TOOL_TO_TOOLSET_MAP, dict)
        assert len(TOOL_TO_TOOLSET_MAP) > 0


class TestDynamicCronSchemaGuidance:
    def test_cronjob_schema_mentions_autonomy_when_enabled(self, monkeypatch):
        monkeypatch.setenv("HERMES_INTERACTIVE", "1")
        monkeypatch.setenv("HERMES_AUTONOMY_ENABLED", "1")
        monkeypatch.setenv("HERMES_AUTONOMY_EXTRACT_BEHAVIOR", "both")
        monkeypatch.setenv("HERMES_SESSION_KEY", "telegram:123")

        tools = get_tool_definitions(
            enabled_toolsets=["cronjob", "cron_read", "autonomy"],
            quiet_mode=True,
        )
        by_name = {tool["function"]["name"]: tool["function"] for tool in tools}

        cron_desc = by_name["cronjob"]["description"]
        inspect_desc = by_name["cron_inspect"]["description"]
        autonomy_desc = by_name["autonomy_watch"]["description"]

        assert "do not use cron for open-ended monitoring" in cron_desc.lower()
        assert "rather than inventing a schedule" in cron_desc.lower()
        assert "inspect first with cron_inspect" in cron_desc.lower()
        assert "do not call cronjob(create) without a user-provided schedule" in cron_desc.lower()
        assert "prefer autonomy_watch" in cron_desc.lower()
        assert "before cronjob" in inspect_desc.lower()
        assert "preferred way to register open-ended monitoring" in autonomy_desc.lower()
        assert "do not call cronjob or monitoring skills first" in autonomy_desc.lower()

    def test_auto_extract_mode_hides_autonomy_watch_and_mentions_hidden_extractor(self, monkeypatch):
        monkeypatch.setenv("HERMES_INTERACTIVE", "1")
        monkeypatch.setenv("HERMES_AUTONOMY_ENABLED", "1")
        monkeypatch.setenv("HERMES_AUTONOMY_EXTRACT_BEHAVIOR", "auto_extract")
        monkeypatch.setenv("HERMES_SESSION_KEY", "telegram:123")

        tools = get_tool_definitions(
            enabled_toolsets=["cronjob", "cron_read", "autonomy"],
            quiet_mode=True,
        )
        by_name = {tool["function"]["name"]: tool["function"] for tool in tools}

        cron_desc = by_name["cronjob"]["description"].lower()

        assert "hidden post-turn autonomy extractor handles those" in cron_desc
        assert "autonomy_watch" not in by_name

    def test_cronjob_schema_default_description_unchanged_without_autonomy(self, monkeypatch):
        monkeypatch.setenv("HERMES_INTERACTIVE", "1")
        monkeypatch.delenv("HERMES_AUTONOMY_ENABLED", raising=False)
        monkeypatch.delenv("HERMES_AUTONOMY_EXTRACT_BEHAVIOR", raising=False)
        monkeypatch.delenv("HERMES_SESSION_KEY", raising=False)

        tools = get_tool_definitions(
            enabled_toolsets=["cronjob", "cron_read", "autonomy"],
            quiet_mode=True,
        )
        by_name = {tool["function"]["name"]: tool["function"] for tool in tools}

        cron_desc = by_name["cronjob"]["description"].lower()
        assert "do not default to cron for open-ended monitoring" not in cron_desc
        assert "autonomy_watch" not in by_name
