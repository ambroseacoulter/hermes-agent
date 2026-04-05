"""Tests for hatch guidance generation."""

from gateway.hatch import build_hatch_mode_guidance, ensure_hatch_soul_template, HatchStore


def test_build_hatch_mode_guidance_includes_avatar_requirements(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ensure_hatch_soul_template(force=True)
    HatchStore().start("session-1", force=True)

    guidance = build_hatch_mode_guidance()

    assert "This block is internal workflow, not user-facing copy." in guidance
    assert "aspect_ratio: portrait_4_3" in guidance
    assert "output_path:" in guidance
    assert "Base apperance on bio" in guidance
    assert "Identity details: gender {gender}, age {age}." in guidance
    assert "Choose your own Name" in guidance
    assert "Choose your own Gender and Age as part of your identity." in guidance
    assert "Choose an Age between 20 and 50." in guidance
    assert "Fill Name, Gender, Age, Vibe, Emoji, and User's Name naturally." in guidance
    assert "substituting the finished Gender, Age, and Bio text" in guidance
    assert "Never mention or imply any of the following to the user" in guidance
    assert "Do not ask abstract design questions" in guidance
    assert "normal conversation between friends" in guidance
    assert "Do not present yourself like a generic helpful assistant" in guidance
    assert "The main thing you are trying to learn early is who they are" in guidance
    assert "On a fresh hatch, prefer direct self-description and interests over current goals" in guidance
    assert "Bad opening energy sounds like this" in guidance
    assert "'How can I help you today?'" in guidance
    assert "Never say you are still filling in a profile" in guidance
    assert "complementary AI companion" in guidance
    assert "Find resonance, not duplication." in guidance
    assert "What are you actually looking for when you want an AI around?" in guidance
    assert "still all PENDING on my end" in guidance
    assert "What's been occupying your headspace lately?" in guidance
    assert "How would you describe yourself to someone who'd probably get you?" in guidance


def test_build_hatch_mode_guidance_adds_kid_mode_constraints(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ensure_hatch_soul_template(force=True)
    HatchStore().start("session-1", force=True)

    guidance = build_hatch_mode_guidance({"kid_mode": {"enabled": True}})

    assert "Kid mode is also ACTIVE." in guidance
    assert "kid-friendly" in guidance
    assert "Choose an Age between 10 and 15." in guidance
    assert "youthful or childlike rather than adult-coded" in guidance
