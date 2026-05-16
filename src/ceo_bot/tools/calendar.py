from __future__ import annotations

from typing import Any

from ceo_bot.integrations.google_calendar import create_event

TOOL_SCHEMA: dict[str, Any] = {
    "name": "create_calendar_event",
    "description": (
        "Create a Google Calendar event on the user's primary calendar. "
        "Requires the user to have completed Google OAuth (use /auth google in Discord)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Event title."},
            "start_iso": {
                "type": "string",
                "description": "ISO 8601 start time with timezone offset (e.g. 2026-05-20T19:00:00-07:00).",
            },
            "end_iso": {
                "type": "string",
                "description": "ISO 8601 end time with timezone offset.",
            },
            "description": {"type": "string", "description": "Optional event description."},
            "location": {"type": "string", "description": "Optional location."},
        },
        "required": ["summary", "start_iso", "end_iso"],
    },
}


async def run(
    *,
    user_id: int,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    location: str = "",
    **_: Any,
) -> dict[str, Any]:
    return await create_event(
        user_id=user_id,
        summary=summary,
        start_iso=start_iso,
        end_iso=end_iso,
        description=description,
        location=location,
    )
