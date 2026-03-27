"""Sendblue-specific messaging actions."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from tools.registry import registry

REACTION_TYPES = ["love", "like", "dislike", "laugh", "emphasize", "question"]

SENDBLUE_ACTION_SCHEMA = {
    "name": "sendblue_action",
    "description": (
        "Send Sendblue-specific conversation actions in the current Sendblue chat. "
        "Useful for iMessage tapback reactions and manual read receipts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["react", "mark_read"],
                "description": "Which Sendblue action to perform."
            },
            "reaction": {
                "type": "string",
                "enum": REACTION_TYPES,
                "description": "Required when action='react'."
            },
            "message_handle": {
                "type": "string",
                "description": "Optional Sendblue message_handle to react to. Defaults to the latest inbound message in the current Sendblue chat."
            },
            "part_index": {
                "type": "integer",
                "description": "Optional multi-part index for reactions. Defaults to 0."
            },
            "chat_id": {
                "type": "string",
                "description": "Optional Sendblue chat id. Defaults to the current Sendblue session chat."
            }
        },
        "required": ["action"]
    }
}


def _check_sendblue_tool_available() -> bool:
    return os.getenv("HERMES_SESSION_PLATFORM", "").strip().lower() == "sendblue"


def _run(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("sendblue_action must be called from a synchronous tool context")


def _handle_sendblue_action(args: dict[str, Any], **kw) -> str:
    from gateway.platforms.sendblue import (
        get_recent_inbound,
        get_sendblue_settings,
        sendblue_mark_read,
        sendblue_send_reaction,
    )

    settings = get_sendblue_settings()
    if not (settings.api_key and settings.api_secret and settings.from_number):
        return json.dumps({"error": "Sendblue is not configured"})

    action = str(args.get("action") or "").strip().lower()
    chat_id = str(args.get("chat_id") or os.getenv("HERMES_SESSION_CHAT_ID", "")).strip()
    if not chat_id:
        return json.dumps({"error": "No Sendblue chat is active"})

    if action == "mark_read":
        if not chat_id.startswith("+"):
            return json.dumps({"error": "mark_read is only supported for direct Sendblue conversations"})
        result = _run(sendblue_mark_read(settings, chat_id))
        return json.dumps({
            "success": result.success,
            "error": result.error,
            "chat_id": chat_id,
            "raw_response": result.raw_response,
        })

    if action == "react":
        message_handle = str(args.get("message_handle") or "").strip()
        if not message_handle:
            recent = get_recent_inbound(chat_id)
            if recent:
                message_handle = str(recent.get("message_handle") or "")
        if not message_handle:
            return json.dumps({"error": "No Sendblue message_handle available to react to in this chat"})

        reaction = str(args.get("reaction") or "").strip().lower()
        if reaction not in REACTION_TYPES:
            return json.dumps({"error": f"Invalid reaction. Use one of: {', '.join(REACTION_TYPES)}"})
        part_index = int(args.get("part_index") or 0)
        result = _run(sendblue_send_reaction(settings, message_handle, reaction, part_index=part_index))
        return json.dumps({
            "success": result.success,
            "error": result.error,
            "message_handle": message_handle,
            "reaction": reaction,
            "raw_response": result.raw_response,
        })

    return json.dumps({"error": "Unknown action. Use 'react' or 'mark_read'."})


registry.register(
    name="sendblue_action",
    toolset="sendblue",
    schema=SENDBLUE_ACTION_SCHEMA,
    handler=_handle_sendblue_action,
    check_fn=_check_sendblue_tool_available,
    emoji="💬",
)
