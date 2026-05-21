from __future__ import annotations

from typing import Any

from ceo_bot.config import settings
from ceo_bot.integrations.google_calendar import create_event


def _roster_hint() -> str:
    members = settings.household_members
    if not members:
        return ""
    listing = ", ".join(f"{m.name}=discord_id {m.discord_id}" for m in members)
    return f" Known household members: {listing}."


TOOL_SCHEMA: dict[str, Any] = {
    "name": "create_calendar_event",
    "description": (
        "Create a Google Calendar event. By default it lands on the requester's "
        "primary calendar. To include other household members, pass their Discord "
        "IDs in attendee_user_ids — members who have OAuth'd get the event written "
        "directly to their primary calendar; members who haven't get a standard "
        "email invite."
        + _roster_hint()
        + " Requires the requester to have completed Google OAuth (/auth google)."
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
            "attendee_user_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Discord IDs of OTHER household members to include on the event. "
                    "Omit or leave empty for solo events. Don't include the requester's "
                    "own ID — they're added automatically."
                ),
            },
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
    attendee_user_ids: list[int] | None = None,
    **_: Any,
) -> dict[str, Any]:
    return await create_event(
        user_id=user_id,
        summary=summary,
        start_iso=start_iso,
        end_iso=end_iso,
        description=description,
        location=location,
        attendee_user_ids=attendee_user_ids or [],
    )
