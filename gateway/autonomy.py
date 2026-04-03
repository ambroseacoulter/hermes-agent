from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Any, Optional


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")
_IMPORTANCE_ORDER = {"low": 0, "normal": 1, "high": 2, "critical": 3}
_ALLOWED_IMPORTANCE = set(_IMPORTANCE_ORDER)
_ALLOWED_CATEGORIES = {"utility", "social"}
_ALLOWED_KINDS = {"topic", "repo", "issue", "person", "deadline", "project", "other"}
_WATCH_NOISE_PATTERNS = (
    r"\bwatcher setup\b",
    r"\bwatch setup\b",
    r"\bmonitoring setup\b",
    r"\bmonitoring\b",
    r"\bwatching\b",
    r"\bwatcher\b",
    r"\btracker\b",
    r"\btracking\b",
    r"\btrack\b",
    r"\bmonitor\b",
    r"\bkeep an eye on\b",
)


@dataclass(frozen=True)
class QuietHoursWindow:
    enabled: bool
    start: str
    end: str


def build_autonomy_default_guidance(
    *,
    interval_seconds: int,
    home_label: str = "",
) -> str:
    cadence_minutes = max(1, round(interval_seconds / 60))
    lines = [
        "Open-ended monitoring requests are considered complete enough for autonomy by default.",
        "Requests like 'keep an eye on X' or 'let me know if anything interesting happens' should create or update a watch immediately.",
        "Do not treat open-ended monitoring as a cron setup problem that needs a made-up schedule before Hermes can help.",
        f"Use the profile autonomy loop cadence by default (currently about every {cadence_minutes} minute{'s' if cadence_minutes != 1 else ''}).",
        "Do not ask the user to confirm cadence, frequency, or delivery destination unless they explicitly requested a specific schedule, a different destination, or special alert criteria that materially change behavior.",
        "Do not mention internal autonomy mechanics, loop cadence, home-chat routing, extraction mode, or other system internals in normal user-facing conversation unless the user explicitly asks.",
        "For broad words like 'interesting', use a sensible default blend of notable product launches, major research, important partnerships, policy/legal changes, safety incidents, and major leadership or funding news instead of asking a follow-up by default.",
        "Prefer autonomy over cron for open-ended monitoring. Reserve cron for precise timed schedules, fixed recurring digests, or exact reminders.",
        "Do not call other monitoring or scheduling tools just to check whether open-ended watching is possible; autonomy watching is already the correct fit.",
        "Never invent a cron cadence, repeat count, expiry window, or polling interval just to make a request fit cron.",
        "Do not copy a cadence from some other cron job onto an autonomy watch. Autonomy watches inherit the profile autonomy loop rather than their own made-up schedule.",
    ]
    if home_label:
        lines.insert(
            3,
            f"Use the configured home chat for proactive delivery by default ({home_label}).",
        )
    else:
        lines.insert(
            3,
            "If a home chat is configured, use it for proactive delivery by default.",
        )
    return " ".join(lines)


def parse_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass

    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def normalize_importance(value: Any, default: str = "normal") -> str:
    text = str(value or "").strip().lower()
    return text if text in _ALLOWED_IMPORTANCE else default


def importance_rank(value: Any) -> int:
    return _IMPORTANCE_ORDER.get(normalize_importance(value), 1)


def normalize_category(value: Any, default: str = "utility") -> str:
    text = str(value or "").strip().lower()
    return text if text in _ALLOWED_CATEGORIES else default


def normalize_watch_kind(value: Any, default: str = "other") -> str:
    text = str(value or "").strip().lower()
    return text if text in _ALLOWED_KINDS else default


def canonical_watch_subject(title: str) -> str:
    text = str(title or "").strip().lower()
    if not text:
        return ""
    for pattern in _WATCH_NOISE_PATTERNS:
        text = re.sub(pattern, " ", text)
    text = text.replace("/", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_watch_title(title: str) -> str:
    text = str(title or "").strip()
    if not text:
        return ""
    # Apply the same substitutions to the original text by removing the noise
    # phrases case-insensitively, then flatten punctuation/whitespace lightly.
    cleaned = text
    for pattern in _WATCH_NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("/", " / ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_/")
    return cleaned or text


def stable_watch_key(kind: str, title: str) -> str:
    canonical = canonical_watch_subject(title) or title.strip().lower()
    seed = f"{normalize_watch_kind(kind)}::{canonical}"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:16]
    slug_source = canonical or title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug_source).strip("-")[:40] or "watch"
    return f"{normalize_watch_kind(kind)}:{slug}:{digest}"


def _token_overlap_ratio(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(len(left_tokens), len(right_tokens))


def _match_existing_watch(
    *,
    kind: str,
    title: str,
    existing_items: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    candidate = canonical_watch_subject(title)
    if not candidate:
        return None
    for item in existing_items:
        if normalize_watch_kind(item.get("kind")) != kind:
            continue
        existing_title = canonical_watch_subject(item.get("title") or "")
        if not existing_title:
            continue
        if candidate == existing_title:
            return item
        if candidate in existing_title or existing_title in candidate:
            if _token_overlap_ratio(candidate, existing_title) >= 0.6:
                return item
        if _token_overlap_ratio(candidate, existing_title) >= 0.8:
            return item
    return None


def normalize_watch_items(
    payload: dict[str, Any],
    default_infer_level: str,
    *,
    existing_items: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    raw = payload.get("watch_items") or []
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    existing_items = existing_items or []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        kind = normalize_watch_kind(item.get("kind"))
        matched_existing = _match_existing_watch(
            kind=kind,
            title=title,
            existing_items=existing_items,
        )
        key = (
            str(item.get("key") or "").strip()
            or str((matched_existing or {}).get("normalized_key") or "").strip()
            or stable_watch_key(kind, title)
        )
        if key in seen:
            continue
        seen.add(key)
        display_title = clean_watch_title(title)
        if matched_existing and matched_existing.get("title"):
            display_title = str(matched_existing["title"]).strip()
        if not display_title:
            display_title = title
        items.append(
            {
                "normalized_key": key,
                "title": display_title,
                "kind": kind,
                "description": str(item.get("description") or "").strip(),
                "importance": normalize_importance(item.get("importance")),
                "inference_mode": str(item.get("inference_mode") or default_infer_level or "implied").strip().lower() or "implied",
                "due_at": parse_iso_datetime(item.get("due_at")),
            }
        )
    return items


def normalize_resolved_watch_keys(
    payload: dict[str, Any],
    *,
    existing_items: Optional[list[dict[str, Any]]] = None,
) -> list[str]:
    raw = payload.get("resolved_watch_keys") or []
    if not isinstance(raw, list):
        return []
    existing_items = existing_items or []
    existing_by_key = {
        str(item.get("normalized_key") or "").strip(): item
        for item in existing_items
        if str(item.get("normalized_key") or "").strip()
    }
    resolved: list[str] = []
    seen: set[str] = set()
    for value in raw:
        text = str(value or "").strip()
        if not text:
            continue
        matched_key = ""
        if text in existing_by_key:
            matched_key = text
        else:
            for item in existing_items:
                if _match_existing_watch(
                    kind=normalize_watch_kind(item.get("kind")),
                    title=text,
                    existing_items=[item],
                ):
                    matched_key = str(item.get("normalized_key") or "").strip()
                    if matched_key:
                        break
        if not matched_key or matched_key in seen:
            continue
        seen.add(matched_key)
        resolved.append(matched_key)
    return resolved


def normalize_supervisor_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = {
        "summary": str(payload.get("summary") or "").strip(),
        "watch_updates": [],
        "findings": [],
        "artifacts": [],
    }

    raw_updates = payload.get("watch_updates") or []
    if isinstance(raw_updates, list):
        for item in raw_updates:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            status = str(item.get("status") or "active").strip().lower()
            if status not in {"active", "resolved", "paused"}:
                status = "active"
            next_check_minutes = item.get("next_check_in_minutes")
            try:
                next_check_minutes = int(next_check_minutes) if next_check_minutes is not None else None
            except (TypeError, ValueError):
                next_check_minutes = None
            result["watch_updates"].append(
                {
                    "key": key,
                    "status": status,
                    "next_check_in_minutes": next_check_minutes,
                    "description": str(item.get("description") or "").strip(),
                    "importance": normalize_importance(item.get("importance")),
                }
            )

    raw_findings = payload.get("findings") or []
    if isinstance(raw_findings, list):
        for item in raw_findings:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            kind = str(item.get("kind") or "observation").strip().lower()
            if kind not in {"event", "observation", "fact", "decision"}:
                kind = "observation"
            result["findings"].append(
                {
                    "watch_key": str(item.get("watch_key") or "").strip(),
                    "kind": kind,
                    "title": title,
                    "summary": str(item.get("summary") or "").strip(),
                    "details": item.get("details") if isinstance(item.get("details"), dict) else {},
                    "importance": normalize_importance(item.get("importance")),
                    "category": normalize_category(item.get("category")),
                    "message_preview": str(item.get("message_preview") or item.get("summary") or title).strip(),
                }
            )

    raw_artifacts = payload.get("artifacts") or []
    if isinstance(raw_artifacts, list):
        for item in raw_artifacts:
            if not isinstance(item, dict):
                continue
            artifact_type = str(item.get("artifact_type") or "").strip().lower()
            title = str(item.get("title") or "").strip()
            if not artifact_type or not title:
                continue
            payload_json = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            target_json = item.get("target") if isinstance(item.get("target"), dict) else {}
            requirements = item.get("execution_requirements") if isinstance(item.get("execution_requirements"), dict) else {}
            result["artifacts"].append(
                {
                    "watch_key": str(item.get("watch_key") or "").strip(),
                    "artifact_type": artifact_type,
                    "title": title,
                    "summary": str(item.get("summary") or "").strip(),
                    "payload": payload_json,
                    "target": target_json,
                    "execution_requirements": requirements,
                    "importance": normalize_importance(item.get("importance")),
                    "category": normalize_category(item.get("category")),
                    "approval_required": bool(item.get("approval_required", False)),
                    "message_preview": str(item.get("message_preview") or item.get("summary") or title).strip(),
                }
            )

    return result


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_inbox_signature(items: list[dict[str, Any]]) -> str:
    compact = [
        {
            "id": item.get("id"),
            "revision": item.get("revision"),
            "status": item.get("status"),
        }
        for item in sorted(items, key=lambda row: (row.get("id") or 0))
    ]
    blob = json.dumps(compact, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:16]


def build_autonomy_digest(items: list[dict[str, Any]], max_items: int = 4) -> str:
    if not items:
        return ""

    ranked = sorted(
        items,
        key=lambda item: (-importance_rank(item.get("importance")), -(item.get("revision") or 0), -(item.get("id") or 0)),
    )[:max_items]

    lines = ["[Autonomy update since your last seen revision]"]
    for item in ranked:
        prefix = "Approval required:" if item.get("approval_required") else "Update:"
        text = str(item.get("message_preview") or item.get("title") or "").strip()
        if not text:
            continue
        lines.append(f"- {prefix} {text}")
    return "\n".join(lines) if len(lines) > 1 else ""


def parse_hhmm(value: str) -> Optional[dt_time]:
    text = str(value or "").strip()
    if not _HHMM_RE.match(text):
        return None
    hour, minute = text.split(":", 1)
    try:
        return dt_time(hour=int(hour), minute=int(minute))
    except ValueError:
        return None


def is_within_quiet_hours(window: QuietHoursWindow, now_dt: datetime) -> bool:
    if not window.enabled:
        return False
    start = parse_hhmm(window.start)
    end = parse_hhmm(window.end)
    if start is None or end is None:
        return False

    current = now_dt.timetz().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end
