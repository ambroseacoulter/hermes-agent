"""Tests for the read-only cron inspection tool."""

import json

from tools.cron_inspect_tool import cron_inspect
from tools.cronjob_tools import schedule_cronjob


class TestCronInspectTool:
    def test_lists_jobs_read_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
        monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
        monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")

        created = json.loads(
            schedule_cronjob(
                prompt="Check OpenAI updates",
                schedule="every 1h",
                name="OpenAI monitor",
            )
        )
        assert created["success"] is True

        result = json.loads(cron_inspect())

        assert result["success"] is True
        assert result["count"] == 1
        assert result["jobs"][0]["name"] == "OpenAI monitor"
        assert result["jobs"][0]["schedule_kind"] == "interval"

    def test_query_filters_results(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
        monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
        monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")

        json.loads(schedule_cronjob(prompt="Check OpenAI updates", schedule="every 1h", name="OpenAI"))
        json.loads(schedule_cronjob(prompt="Check Anthropic updates", schedule="every 1h", name="Anthropic"))

        result = json.loads(cron_inspect(query="openai"))

        assert result["success"] is True
        assert result["count"] == 1
        assert result["jobs"][0]["name"] == "OpenAI"
