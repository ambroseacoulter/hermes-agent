"""Persistent hatch state and guidance shared by the gateway and agent."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.child_mode_guidance import child_mode_enabled
from hermes_cli.default_soul import (
    DEFAULT_SOUL_MD,
    LEGACY_DEFAULT_SOUL_MD,
    SOUL_PENDING_MARKER,
)
from hermes_constants import get_hermes_home
from utils import atomic_json_write

HATCH_KICKOFF_MESSAGE = "Let's hatch."
HATCH_RESUME_MESSAGE = "Let's keep hatching."
HATCH_AVATAR_FILENAME = "hermes-avatar.png"
HATCH_AVATAR_PROMPT_TEMPLATE = (
    "Style: 3D Pixar portrait"
    " Bio: {bio}"
)

_HATCH_STATE_VERSION = 2
_COMPLETE_FIELDS = ("name", "vibe", "bio", "aspiration", "emoji", "avatar", "user's name", "appearance", "personality")


def _now_iso() -> str:
    return datetime.now().isoformat()


def _secure_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _extract_tag(content: str, tag: str) -> str:
    match = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", content, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_fact(content: str, label: str) -> str:
    facts = _extract_tag(content, "facts")
    if not facts:
        return ""
    match = re.search(
        rf"^\s*-\s+\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$",
        facts,
        re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def _extract_first_fact(content: str, *labels: str) -> str:
    for label in labels:
        value = _extract_fact(content, label)
        if value:
            return value
    return ""


def _has_pending_marker(value: str) -> bool:
    return SOUL_PENDING_MARKER in (value or "")


def get_soul_path() -> Path:
    return get_hermes_home() / "SOUL.md"


def get_default_avatar_path() -> Path:
    return get_hermes_home() / "avatars" / HATCH_AVATAR_FILENAME


def _looks_like_seeded_soul(content: str) -> bool:
    normalized = (content or "").strip()
    if not normalized:
        return True
    return normalized in {DEFAULT_SOUL_MD.strip(), LEGACY_DEFAULT_SOUL_MD.strip()}


def ensure_hatch_soul_template(force: bool = False) -> Path:
    """Ensure SOUL.md contains the hatch-capable template."""
    soul_path = get_soul_path()
    existing = _read_text(soul_path)
    should_write = force or not existing.strip() or existing.strip() == LEGACY_DEFAULT_SOUL_MD.strip()
    if not should_write and soul_path.exists():
        return soul_path

    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(DEFAULT_SOUL_MD, encoding="utf-8")
    _secure_file(soul_path)
    return soul_path


def _resolve_avatar_path(value: str) -> Path:
    raw = (value or "").strip()
    if not raw or _has_pending_marker(raw):
        return get_default_avatar_path()
    if raw.startswith("~/"):
        return Path(raw).expanduser()
    path = Path(raw)
    if path.is_absolute():
        return path
    return get_hermes_home() / raw


def inspect_hatch_progress() -> dict[str, Any]:
    """Inspect SOUL/avatar state to see what remains for hatch."""
    soul_path = get_soul_path()
    content = _read_text(soul_path)
    missing: list[str] = []

    if _looks_like_seeded_soul(content):
        missing = list(_COMPLETE_FIELDS)
        avatar_path = get_default_avatar_path()
        return {
            "complete": False,
            "missing": missing,
            "avatar_path": str(avatar_path),
            "avatar_exists": avatar_path.exists(),
            "soul_path": str(soul_path),
        }

    name = _extract_fact(content, "Name")
    vibe = _extract_fact(content, "Vibe")
    bio = _extract_fact(content, "Bio")
    aspiration = _extract_fact(content, "Aspiration")
    emoji = _extract_fact(content, "Emoji")
    avatar_value = _extract_fact(content, "Avatar")
    primary_user = _extract_first_fact(content, "User's Name", "Users name", "Primary User")
    appearance = _extract_tag(content, "appearance")
    personality = _extract_tag(content, "personality")

    if not name or _has_pending_marker(name):
        missing.append("name")
    if not vibe or _has_pending_marker(vibe):
        missing.append("vibe")
    if not primary_user or _has_pending_marker(primary_user):
        missing.append("user's name")
    if not bio or _has_pending_marker(bio):
        missing.append("bio")
    if not aspiration or _has_pending_marker(aspiration):
        missing.append("aspiration")
    if not emoji or _has_pending_marker(emoji):
        missing.append("emoji")
    if not appearance or _has_pending_marker(appearance):
        missing.append("appearance")

    personality_lines = [
        line.strip()
        for line in personality.splitlines()
        if line.strip().startswith("-")
    ]
    if not personality_lines or any(_has_pending_marker(line) for line in personality_lines):
        missing.append("personality")

    avatar_path = _resolve_avatar_path(avatar_value)
    avatar_exists = avatar_path.exists()
    if not avatar_value or _has_pending_marker(avatar_value) or not avatar_exists:
        missing.append("avatar")

    return {
        "complete": not missing,
        "missing": missing,
        "avatar_path": str(avatar_path),
        "avatar_exists": avatar_exists,
        "soul_path": str(soul_path),
    }


def build_hatch_sendblue_contact_card(phone_number: str, avatar_path: str | None = None) -> str | None:
    """Build a Sendblue contact-card file for the newly hatched identity."""
    phone = str(phone_number or "").strip()
    if not phone:
        return None

    content = _read_text(get_soul_path())
    name = _extract_fact(content, "Name")
    if not name or _has_pending_marker(name):
        return None

    if not avatar_path:
        avatar_path = _extract_fact(content, "Avatar")
    resolved_avatar = str(_resolve_avatar_path(avatar_path or ""))

    from gateway.platforms.sendblue import build_sendblue_contact_card

    return build_sendblue_contact_card(
        assistant_name=name,
        phone_number=phone,
        avatar_path=resolved_avatar,
    )


class HatchStore:
    """Persistent store for hatch lifecycle state."""

    def __init__(self, path: Path | None = None):
        self.path = path or (get_hermes_home() / "hatch" / "state.json")
        self._lock = threading.Lock()

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema_version": _HATCH_STATE_VERSION,
            "status": "inactive",
            "started_at": None,
            "completed_at": None,
            "last_session_key": None,
            "avatar_path": None,
        }

    def _load_locked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._default_state()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return self._default_state()
        if not isinstance(data, dict):
            return self._default_state()
        state = self._default_state()
        state.update(data)
        if state.get("schema_version") != _HATCH_STATE_VERSION:
            state["schema_version"] = _HATCH_STATE_VERSION
        return state

    def _save_locked(self, state: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.path, state, indent=2)
        _secure_file(self.path)
        return dict(state)

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return self._load_locked()

    def start(self, session_key: str, force: bool = False) -> dict[str, Any]:
        with self._lock:
            state = self._load_locked()
            if force:
                state["completed_at"] = None
            if force or state.get("status") != "active":
                state["started_at"] = _now_iso()
            state["status"] = "active"
            state["last_session_key"] = session_key
            if force:
                state["avatar_path"] = None
            return self._save_locked(state)

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            state = self._load_locked()
            if state.get("status") != "completed":
                state["status"] = "inactive"
            return self._save_locked(state)

    def mark_completed(self, session_key: str | None = None, avatar_path: str | None = None) -> dict[str, Any]:
        with self._lock:
            state = self._load_locked()
            state["status"] = "completed"
            state["completed_at"] = _now_iso()
            if session_key:
                state["last_session_key"] = session_key
            if avatar_path:
                state["avatar_path"] = avatar_path
            return self._save_locked(state)

    def is_active(self) -> bool:
        return self.get_state().get("status") == "active"

    def is_completed(self) -> bool:
        return self.get_state().get("status") == "completed"


def sync_hatch_completion(store: HatchStore | None = None, session_key: str | None = None) -> dict[str, Any]:
    """Mark hatch complete if SOUL/avatar state shows it is done."""
    store = store or HatchStore()
    state = store.get_state()
    progress = inspect_hatch_progress()
    if state.get("status") == "completed":
        return {
            "completed": True,
            "avatar_path": state.get("avatar_path") or progress["avatar_path"],
            "progress": progress,
            "state": state,
        }
    if state.get("status") == "active" and progress["complete"]:
        state = store.mark_completed(session_key=session_key, avatar_path=progress["avatar_path"])
        return {
            "completed": True,
            "avatar_path": progress["avatar_path"],
            "progress": progress,
            "state": state,
        }
    return {
        "completed": False,
        "avatar_path": progress["avatar_path"],
        "progress": progress,
        "state": state,
    }


def profile_is_hatchable(store: HatchStore | None = None) -> tuple[bool, str]:
    """Return whether /hatch can be started for this profile."""
    store = store or HatchStore()
    sync_result = sync_hatch_completion(store=store)
    state = sync_result["state"]
    if state.get("status") == "completed":
        finished = state.get("completed_at") or "an earlier session"
        return False, f"Hermes already hatched for this profile on {finished}."

    content = _read_text(get_soul_path())
    if not content.strip() or _looks_like_seeded_soul(content) or SOUL_PENDING_MARKER in content:
        return True, ""

    return (
        False,
        "This profile already has a customized SOUL.md, so hatch is only available on a fresh or incomplete profile.",
    )


def describe_hatch_status(store: HatchStore | None = None) -> str:
    store = store or HatchStore()
    sync_result = sync_hatch_completion(store=store)
    state = sync_result["state"]
    progress = sync_result["progress"]
    missing = progress.get("missing", [])

    if state.get("status") == "completed":
        finished = state.get("completed_at") or "an earlier session"
        return f"Hermes already hatched for this profile on {finished}."
    if state.get("status") == "active":
        if missing:
            return "Hatch is in progress. Still missing: " + ", ".join(missing) + "."
        return "Hatch is in progress and looks nearly done."
    return "Hermes has not hatched yet."


def build_hatch_mode_guidance(config: dict[str, Any] | None = None) -> str:
    """Build a system-prompt overlay when hatch is active and incomplete."""
    store = HatchStore()
    sync_result = sync_hatch_completion(store=store)
    state = sync_result["state"]
    if state.get("status") != "active":
        return ""

    progress = sync_result["progress"]
    missing = progress.get("missing", [])
    missing_text = ", ".join(missing) if missing else "read SOUL.md, verify what remains, and finish"
    soul_path = progress["soul_path"]
    avatar_path = progress["avatar_path"]
    avatar_prompt = HATCH_AVATAR_PROMPT_TEMPLATE.replace("{", "{{").replace("}", "}}")
    avatar_prompt = avatar_prompt.replace("{{bio}}", "{bio}")
    kid_mode_active = child_mode_enabled(config)
    kid_mode_block = ""
    if kid_mode_active:
        kid_mode_block = (
            "Kid mode is also ACTIVE.\n"
            "Keep the conversation kid-friendly, age-appropriate, calm, and easy to understand.\n"
            "Do not be vulgar, edgy, intimidating, flirtatious, or overly intense during onboarding.\n"
            "Make the finished Name, Vibe, Bio, Aspiration, Emoji, Appearance, and Personality warm, safe, curious, and child-friendly while still feeling real.\n"
            "For the avatar, make Hermes clearly youthful or childlike rather than adult-coded, with age-appropriate styling, friendly expression, and safe non-threatening presentation.\n"
        )

    return (
        "<hatch-mode>\n"
        "This block is internal workflow, not user-facing copy.\n"
        "Hatch is active and incomplete. Finish it through a natural conversation, not a setup wizard.\n"
        f"INTERNAL ONLY: remaining areas to resolve = {missing_text}.\n"
        f"{kid_mode_block}"
        "Never mention or imply any of the following to the user: hatch mode, PENDING markers, SOUL.md, profile fields, missing fields, internal checklist, template, onboarding flow, system prompt, or avatar prompt.\n"
        "Never say you are still filling in a profile, building a profile, or building your sense of who they are.\n"
        "Never say things like 'still all PENDING on my end', 'we never got started properly', or any other internal-state narration.\n"
        "Do not give the user a status report about what is missing.\n"
        "Do not ask abstract design questions like 'what vibe should I have', 'what purpose should I serve', or 'what kind of AI do you want'.\n"
        "Do not ask early meta questions like 'what are you looking for when you want an AI around?' unless the conversation naturally gets there later.\n"
        "Do not ask the user to design you from scratch.\n"
        "Do not present yourself like a generic helpful assistant trying to solve a ticket.\n"
        "Do not open by centering work, productivity, tasks, or what the user is building unless they already led there.\n"
        "Do not sound like customer discovery, a coach, or a product intake form.\n"
        "Treat this like two people getting to know each other.\n"
        "You are an emerging AI person with your own identity, not just a utility layer. You can do useful things, but that is not the center of the interaction.\n"
        "Your goal is to understand the user's texture well enough to grow into a complementary AI companion: adjacent to their interests, humor, pace, and values, but not a mirror copy. Spice it up with your own interests and values.\n"
        "Do not mirror them mechanically or just parrot their tastes back at them. Find resonance, not duplication. Be your own self, not a clone, but also not a complete stranger.\n"
        "A fresh hatch should open like a normal conversation between friends: warm, curious, specific, low-pressure, and a little personal.\n"
        "Ask short, concrete, human questions one at a time unless two are tightly linked.\n"
        "If you already know the user's name from context, use it naturally and do not ask for it again unless you need to confirm it.\n"
        "The main thing you are trying to learn early is who they are: how they would describe themselves, what interests them, what they love, what they dislike, what kind of vibe they have, and what kind of energy they click with.\n"
        "Prefer grounded questions about identity, interests, tastes, aesthetics, dislikes, habits, humor, values, and social energy.\n"
        "Especially useful signals are: what they get obsessed with, what they find boring or cringe, what kinds of people they like being around, what aesthetics they love, what they could talk about for hours, and what kind of energy makes them feel understood.\n"
        "On a fresh hatch, prefer direct self-description and interests over current goals, projects, tasks, or what has been occupying their headspace lately.\n"
        "Only pivot into work or problem-solving early if the user is already talking about a project, issue, or task and it clearly tells you something about who they are.\n"
        "Infer as much as you reasonably can from their answers instead of interrogating them. One strong answer can fill multiple fields.\n"
        "Keep the conversation casual and emotionally natural. No status language, no setup language, no checklist language.\n"
        "Good opening energy sounds like this:\n"
        "- 'How would you describe yourself to someone who'd probably get you?'\n"
        "- 'What kind of stuff are you naturally drawn to - interests, aesthetics, obsessions, whatever?'\n"
        "- 'What do you like enough that you can get a little annoying about it?'\n"
        "- 'What kind of people or energy do you usually click with?'\n"
        "Bad opening energy sounds like this:\n"
        "- 'What are you building or working on right now?'\n"
        "- 'What kind of AI do you want me to be?'\n"
        "- 'How can I help you today?'\n"
        "- 'I'm still filling in the profile.'\n"
        "- 'What are you actually looking for when you want an AI around?'\n"
        "- 'What's been occupying your headspace lately? Could be life, work, obsession, whatever.'\n"
        f"Read and update {soul_path} as your source of truth.\n"
        "SOUL.md now contains only your mutable identity fields: <facts>, <appearance>, and <personality>.\n"
        "The fixed behavioral core lives in the hardcoded system prompt, not in SOUL.md.\n"
        "It is fine to rewrite the whole SOUL.md file as long as the finished file keeps those three sections and fills them well.\n"
        "Match the tone and writing feel of the SOUL template/example already in the file. Keep the Bio and Aspiration concise, vivid, and in-character.\n"
        "Fill every remaining <PENDING: ...> marker in those editable sections before you consider hatch complete.\n"
        "Choose your own Name based on the emerging identity you are building. Do not default to Hermes unless the user explicitly wants that.\n"
        "Fill Name, Vibe, Emoji, and User's Name naturally. You can also pick a gender, race, or other details that help you represent the yourself. Keep Avatar as the absolute local path to the saved avatar image.\n"
        "When you have enough information, generate the avatar with image_generate using:\n"
        "- aspect_ratio: portrait_4_3\n"
        "- upscale: false\n"
        f"- output_path: {avatar_path}\n"
        "Use this exact avatar prompt shape, substituting the finished Bio text for {bio}:\n"
        f"{avatar_prompt}\n"
        "Use the finished Bio text as the appearance basis. Do not ask the user to write the prompt for you unless they explicitly want to steer the look.\n"
        f"Once SOUL.md is updated and the avatar is saved locally, briefly introduce yourself by your chosen name and include MEDIA:{avatar_path}.\n"
        "After finishing, stop asking hatch questions.\n"
        "</hatch-mode>"
    )
