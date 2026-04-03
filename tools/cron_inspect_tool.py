"""Read-only cron inspection tool for Hermes and autonomy."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from cron.jobs import list_jobs
from tools.registry import registry


def check_cron_inspect_requirements() -> bool:
    """Available anywhere cronjob would normally be available."""
    return bool(
        os.getenv("HERMES_INTERACTIVE")
        or os.getenv("HERMES_GATEWAY_SESSION")
        or os.getenv("HERMES_EXEC_ASK")
    )


def _job_summary(job: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(job.get("prompt") or "").strip()
    return {
        "job_id": job.get("id"),
        "name": job.get("name"),
        "schedule": job.get("schedule_display"),
        "schedule_kind": (job.get("schedule") or {}).get("kind"),
        "state": job.get("state", "scheduled" if job.get("enabled", True) else "paused"),
        "enabled": bool(job.get("enabled", True)),
        "deliver": job.get("deliver", "local"),
        "skills": list(job.get("skills") or []),
        "prompt_preview": prompt[:200] + "..." if len(prompt) > 200 else prompt,
        "next_run_at": job.get("next_run_at"),
        "last_run_at": job.get("last_run_at"),
    }


def cron_inspect(
    query: Optional[str] = None,
    include_disabled: bool = False,
    limit: int = 20,
    task_id: str = None,
) -> str:
    del task_id
    query_text = str(query or "").strip().lower()
    results: List[Dict[str, Any]] = []
    for job in list_jobs(include_disabled=include_disabled):
        summary = _job_summary(job)
        if query_text:
            haystack = " ".join(
                [
                    str(summary.get("name") or ""),
                    str(summary.get("prompt_preview") or ""),
                    " ".join(summary.get("skills") or []),
                ]
            ).lower()
            if query_text not in haystack:
                continue
        results.append(summary)
        if len(results) >= max(1, int(limit)):
            break
    return json.dumps(
        {
            "success": True,
            "count": len(results),
            "jobs": results,
            "query": query_text or None,
        },
        indent=2,
    )


CRON_INSPECT_SCHEMA = {
    "name": "cron_inspect",
    "description": (
        "Read-only cron inspection. List existing cron jobs to avoid duplicates or see whether "
        "a topic is already covered by a scheduled job. This tool cannot create, edit, pause, "
        "resume, or delete jobs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional text filter to narrow jobs by name, prompt, or attached skills.",
            },
            "include_disabled": {
                "type": "boolean",
                "description": "Include paused/completed jobs in the results.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of jobs to return.",
            },
        },
        "required": [],
    },
}


registry.register(
    name="cron_inspect",
    toolset="cron_read",
    schema=CRON_INSPECT_SCHEMA,
    handler=lambda args, **kw: cron_inspect(
        query=args.get("query"),
        include_disabled=args.get("include_disabled", False),
        limit=args.get("limit", 20),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_cron_inspect_requirements,
    emoji="🗓️",
)
