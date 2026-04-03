"""Tests for gateway.autonomy helper functions."""

from datetime import datetime

from gateway.autonomy import (
    QuietHoursWindow,
    build_autonomy_default_guidance,
    build_autonomy_digest,
    build_inbox_signature,
    clean_watch_title,
    is_within_quiet_hours,
    normalize_resolved_watch_keys,
    normalize_supervisor_payload,
    normalize_watch_items,
    parse_json_object,
)


class TestParseJsonObject:
    def test_extracts_json_from_wrapped_text(self):
        payload = parse_json_object("Here you go:\n{\"watch_items\": []}\nThanks")
        assert payload == {"watch_items": []}


class TestNormalizeWatchItems:
    def test_deduplicates_and_generates_keys(self):
        items = normalize_watch_items(
            {
                "watch_items": [
                    {"title": "Hermes release", "kind": "project"},
                    {"title": "Hermes release", "kind": "project"},
                ]
            },
            "implied",
        )

        assert len(items) == 1
        assert items[0]["normalized_key"].startswith("project:")
        assert items[0]["inference_mode"] == "implied"

    def test_reuses_existing_key_for_near_duplicate_watch(self):
        items = normalize_watch_items(
            {
                "watch_items": [
                    {"title": "xAI/Grok news monitoring", "kind": "topic"},
                ]
            },
            "implied",
            existing_items=[
                {
                    "normalized_key": "topic:xai-grok:abc123",
                    "title": "xAI/Grok monitoring",
                    "kind": "topic",
                }
            ],
        )

        assert len(items) == 1
        assert items[0]["normalized_key"] == "topic:xai-grok:abc123"
        assert items[0]["title"] == "xAI/Grok monitoring"

    def test_clean_watch_title_strips_meta_setup_language(self):
        assert clean_watch_title("OpenAI news watcher setup") == "OpenAI news"

    def test_resolved_watch_keys_match_existing_by_title(self):
        resolved = normalize_resolved_watch_keys(
            {
                "resolved_watch_keys": ["OpenAI news monitoring"],
            },
            existing_items=[
                {
                    "normalized_key": "topic:openai-news:abc",
                    "title": "OpenAI news",
                    "kind": "topic",
                }
            ],
        )

        assert resolved == ["topic:openai-news:abc"]


class TestNormalizeSupervisorPayload:
    def test_filters_invalid_rows(self):
        payload = normalize_supervisor_payload(
            {
                "summary": "done",
                "watch_updates": [{"key": "a", "status": "invalid"}],
                "findings": [{"title": "Useful", "summary": "Hi"}],
                "artifacts": [{"artifact_type": "draft_email", "title": "Draft"}],
            }
        )

        assert payload["summary"] == "done"
        assert payload["watch_updates"][0]["status"] == "active"
        assert payload["findings"][0]["kind"] == "observation"
        assert payload["artifacts"][0]["artifact_type"] == "draft_email"


class TestDigestAndQuietHours:
    def test_default_guidance_uses_home_and_discourages_clarifying_frequency(self):
        guidance = build_autonomy_default_guidance(
            interval_seconds=120,
            home_label="telegram:12345",
        )

        assert "telegram:12345" in guidance
        assert "Do not ask the user to confirm cadence, frequency, or delivery destination" in guidance
        assert "about every 2 minutes" in guidance
        assert "Never invent a cron cadence" in guidance
        assert "Do not treat open-ended monitoring as a cron setup problem" in guidance
        assert "Do not mention internal autonomy mechanics" in guidance
        assert "use a sensible default blend" in guidance

    def test_digest_prioritizes_approval_and_importance(self):
        digest = build_autonomy_digest(
            [
                {
                    "id": 1,
                    "revision": 2,
                    "importance": "normal",
                    "approval_required": False,
                    "message_preview": "Normal update",
                },
                {
                    "id": 2,
                    "revision": 3,
                    "importance": "high",
                    "approval_required": True,
                    "message_preview": "Please approve this send",
                },
            ]
        )

        assert "Please approve this send" in digest
        assert "Approval required:" in digest

    def test_inbox_signature_changes_with_revision(self):
        sig_a = build_inbox_signature([{"id": 1, "revision": 1, "status": "pending"}])
        sig_b = build_inbox_signature([{"id": 1, "revision": 2, "status": "pending"}])

        assert sig_a != sig_b

    def test_quiet_hours_wraps_midnight(self):
        window = QuietHoursWindow(enabled=True, start="22:00", end="07:00")

        assert is_within_quiet_hours(window, datetime(2026, 4, 3, 23, 15)) is True
        assert is_within_quiet_hours(window, datetime(2026, 4, 4, 6, 45)) is True
        assert is_within_quiet_hours(window, datetime(2026, 4, 4, 12, 0)) is False
