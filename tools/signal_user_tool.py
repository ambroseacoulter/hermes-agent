"""Cortex signal emission tool schema."""

import json

from tools.registry import registry


def signal_user_tool(
    title: str,
    summary: str,
    reason: str,
    priority: str = "normal",
    action_items=None,
    metadata=None,
    callback=None,
):
    """Gateway-owned tool for surfacing Cortex signals from webhook runs."""
    if callback is None:
        return json.dumps(
            {
                "success": False,
                "error": "signal_user is only available in webhook signal runs.",
            }
        )
    return callback(
        title=title,
        summary=summary,
        reason=reason,
        priority=priority,
        action_items=action_items,
        metadata=metadata,
    )


registry.register(
    name="signal_user",
    toolset="cortex-signal",
    schema={
        "name": "signal_user",
        "description": (
            "Notify Hermes about an external event that requires user attention, "
            "approval, or more input. The final assistant response will not be shown "
            "to the user in signal-mode webhook runs, so use this tool when the user "
            "must be notified."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short headline for the user-facing notification.",
                },
                "summary": {
                    "type": "string",
                    "description": "Concise factual summary of what happened.",
                },
                "reason": {
                    "type": "string",
                    "enum": ["notify", "approval_required", "input_required"],
                    "description": "Why the user should see this signal.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["normal", "urgent"],
                    "description": "Urgent may interrupt an active turn.",
                },
                "action_items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Concrete next steps Hermes should present to the user.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Structured IDs and references Hermes may need later.",
                },
            },
            "required": ["title", "summary", "reason"],
            "additionalProperties": False,
        },
    },
    handler=lambda args, **kw: signal_user_tool(
        title=args.get("title", ""),
        summary=args.get("summary", ""),
        reason=args.get("reason", "notify"),
        priority=args.get("priority", "normal"),
        action_items=args.get("action_items"),
        metadata=args.get("metadata"),
        callback=kw.get("signal_callback"),
    ),
)
