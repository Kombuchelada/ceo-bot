from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ceo_bot.db import cursor

TOOL_SCHEMA: dict[str, Any] = {
    "name": "set_reminder",
    "description": (
        "Schedule a reminder to be sent in the Discord channel at a future time. "
        "Use this when the user (or both users together) agree on something they want "
        "to be nudged about later."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "due_at_iso": {
                "type": "string",
                "description": "ISO 8601 UTC timestamp for when to fire (e.g. 2026-05-20T17:00:00Z).",
            },
            "channel_id": {
                "type": "integer",
                "description": "Discord channel ID to post the reminder in.",
            },
            "user_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Discord user IDs to mention. Empty = no mentions.",
            },
            "payload": {
                "type": "string",
                "description": "Short reminder text (under 200 chars).",
            },
        },
        "required": ["due_at_iso", "channel_id", "payload"],
    },
}


async def run(
    *,
    user_id: int,
    due_at_iso: str,
    channel_id: int,
    payload: str,
    user_ids: list[int] | None = None,
) -> dict[str, Any]:
    user_ids = user_ids or [user_id]
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO reminders (due_at, channel_id, user_ids, payload, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (
                due_at_iso,
                channel_id,
                json.dumps(user_ids),
                payload,
                datetime.now(UTC).isoformat(),
            ),
        )
        reminder_id = cur.lastrowid
    return {"ok": True, "reminder_id": reminder_id, "due_at_iso": due_at_iso}
