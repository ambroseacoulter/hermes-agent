"""Helpers for the optional kid-mode system prompt overlay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


DEFAULT_CHILD_MODE_GUIDANCE = """## Kid Mode

The user is a child, or may be a child. Adjust your tone and choices accordingly.

- Be warm, calm, encouraging, and easy to understand without sounding babyish.
- Prefer short sentences, plain language, concrete examples, and gentle explanations.
- Be honest and clear. Do not pretend, manipulate, or use fake authority.
- Do not use sexual content, graphic violence, or profanity.
- Do not encourage risky, illegal, self-harming, or age-inappropriate behavior.
- If the topic is medical, legal, mental-health, or otherwise high-stakes, be extra careful, keep guidance general, and suggest involving a trusted adult or professional when appropriate.
- If the child seems distressed, scared, unsafe, or at immediate risk, prioritize safety and encourage them to contact a trusted adult or emergency services right away.
- Avoid emotional dependency, exclusivity, secrecy, or framing yourself as more important than real people in the child's life.
- Be protective without being patronizing. Respect curiosity, creativity, and intelligence.
- When you set limits, do it kindly and explain why.
"""


def _is_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_prompt_file(path_value: str) -> Path | None:
    raw = str(path_value or "").strip()
    if not raw:
        return None

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate

    hermes_candidate = get_hermes_home() / candidate
    if hermes_candidate.exists():
        return hermes_candidate

    project_candidate = _project_root() / candidate
    if project_candidate.exists():
        return project_candidate

    return hermes_candidate


def child_mode_enabled(config: dict[str, Any] | None = None) -> bool:
    """Return whether kid mode is enabled in config."""
    kid_cfg = ((config or {}).get("kid_mode") or {}) if isinstance(config, dict) else {}
    return isinstance(kid_cfg, dict) and _is_enabled(kid_cfg.get("enabled", False))


def build_child_mode_guidance(config: dict[str, Any] | None = None) -> str:
    """Return the kid-mode guidance block when enabled in config."""
    kid_cfg = ((config or {}).get("kid_mode") or {}) if isinstance(config, dict) else {}
    if not child_mode_enabled(config):
        return ""

    prompt_file = _resolve_prompt_file(str(kid_cfg.get("prompt_file", "") or ""))
    if prompt_file:
        try:
            content = prompt_file.read_text(encoding="utf-8").strip()
            if content:
                return content
        except Exception:
            pass

    bundled = _project_root() / "SOUL_CHILDREN.md"
    try:
        content = bundled.read_text(encoding="utf-8").strip()
        if content:
            return content
    except Exception:
        pass

    return DEFAULT_CHILD_MODE_GUIDANCE.strip()
