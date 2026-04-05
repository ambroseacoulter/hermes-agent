"""Tests for kid-mode prompt guidance loading."""

from agent.child_mode_guidance import build_child_mode_guidance


def test_child_mode_disabled_returns_empty():
    assert build_child_mode_guidance({"kid_mode": {"enabled": False}}) == ""


def test_child_mode_uses_relative_file_from_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    prompt_file = tmp_path / "kid-mode.md"
    prompt_file.write_text("Custom child guidance", encoding="utf-8")

    result = build_child_mode_guidance(
        {"kid_mode": {"enabled": True, "prompt_file": "kid-mode.md"}}
    )

    assert result == "Custom child guidance"


def test_child_mode_falls_back_to_bundled_text(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    result = build_child_mode_guidance({"kid_mode": {"enabled": True}})

    assert "user is a child" in result.lower()
