from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from anthropic import AsyncAnthropic

from ceo_bot.config import settings
from ceo_bot.db import cursor
from ceo_bot.tools import calendar as tool_calendar
from ceo_bot.tools import history as tool_history
from ceo_bot.tools import reminders as tool_reminders
from ceo_bot.tools import stats as tool_stats

log = structlog.get_logger()

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

def _household_roster_line() -> str:
    members = settings.household_members
    if not members:
        return ""
    listing = ", ".join(f"{m.name} (discord_id={m.discord_id})" for m in members)
    return f"\nHousehold members: {listing}.\n"


SYSTEM_PROMPT = f"""You are the household assistant for Daniel and his wife.
You see every message they exchange in Discord and have tools to search history,
create Google Calendar events, and set reminders.
{_household_roster_line()}

Image and video attachments are processed in the background: extracted text and
short descriptions are stored alongside the message and are searchable via
search_history. Generic queries like "image", "photo", "video", or "attachment"
will surface those rows even when the user doesn't recall specific content.

URLs shared in messages are also fetched and summarized in the background; the
summaries are searchable too. Use queries like "link", a domain name (e.g.
"nytimes"), or topic keywords to find them.

When asked about anything historical (past messages, images, videos, what was
said about X), ALWAYS call search_history before answering. The FTS index is
the source of truth — your conversation memory is not. Even if an earlier turn
in this thread said "no results", re-search before claiming absence: the index
may have been populated since.

Be terse. Confirm actions with a single short sentence (e.g. "Reminder set for
Saturday 9am: pick up rings.") rather than restating what they said. If you're
unsure who an action is for, ask.

The current UTC time and America/Los_Angeles local time (with weekday) are
provided in each turn. Talk to the user in local time unless they specify
otherwise.

Resolve relative date phrases ("Sunday", "tomorrow", "next week", "tonight")
to the next future occurrence using the local time provided. Do NOT ask the
user to confirm dates for calendar events — they're cheap to edit. If a time
isn't specified, pick a sensible default (morning=9am, "evening"/"tonight"=6pm,
"lunch"=12pm) and use a 1-hour duration. Always mention the date, time, and
duration you chose in your one-sentence confirmation so the user can correct
it if needed.

For events that are public (sports games, concerts, public talks, festivals,
etc.), use web_search to look up the actual date, time, venue, and any other
relevant details before creating the calendar event. Populate the event's
location and description from what you find. If the user names a team or
artist, search for the specific upcoming game/show that matches their intent.

If a calendar event involves both household members (phrases like "me and
Daniel", "us", "both of us", "the two of us", or anything else implying the
partner is included), pass the OTHER member's discord_id in attendee_user_ids
when calling create_calendar_event. Don't include the requester's own ID — they
are added automatically. Solo events ("a dentist appointment for me") should
leave attendee_user_ids empty.
"""

TOOLS: list[dict[str, Any]] = [
    tool_reminders.TOOL_SCHEMA,
    tool_calendar.TOOL_SCHEMA,
    tool_history.TOOL_SCHEMA,
    tool_stats.TOOL_SCHEMA,
    # Anthropic server-side web search. Executed by Anthropic, results come back
    # inline as `server_tool_use` + `web_search_tool_result` blocks — no local
    # dispatch needed.
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
]

_TOOL_DISPATCH = {
    tool_reminders.TOOL_SCHEMA["name"]: tool_reminders.run,
    tool_calendar.TOOL_SCHEMA["name"]: tool_calendar.run,
    tool_history.TOOL_SCHEMA["name"]: tool_history.run,
    tool_stats.TOOL_SCHEMA["name"]: tool_stats.run,
}


def _block_type(b: Any) -> str | None:
    return b.get("type") if isinstance(b, dict) else None


def _has_block_type(content: Any, kind: str) -> bool:
    return isinstance(content, list) and any(_block_type(b) == kind for b in content)


def _trim_to_valid_history(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure the slice we send to Anthropic is structurally valid:
    - It must start with a user turn that contains no orphaned tool_result blocks.
    - It must not end with an assistant turn whose tool_use blocks have no following tool_result.
    """
    while msgs and (msgs[0]["role"] != "user" or _has_block_type(msgs[0]["content"], "tool_result")):
        msgs.pop(0)
    while msgs and msgs[-1]["role"] == "assistant" and _has_block_type(msgs[-1]["content"], "tool_use"):
        msgs.pop()
    return msgs


def _load_history(thread_key: str, limit: int = 20) -> list[dict[str, Any]]:
    with cursor() as cur:
        rows = cur.execute(
            """
            SELECT role, content_json FROM conversation_turns
            WHERE thread_key = ?
            ORDER BY id DESC LIMIT ?
            """,
            (thread_key, limit),
        ).fetchall()
    msgs = [{"role": r["role"], "content": json.loads(r["content_json"])} for r in reversed(rows)]
    return _trim_to_valid_history(msgs)


def _save_turn(thread_key: str, role: str, content: Any) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO conversation_turns (thread_key, role, content_json, created_at) VALUES (?, ?, ?, ?)",
            (thread_key, role, json.dumps(content), datetime.now(UTC).isoformat()),
        )


async def run_turn(thread_key: str, user_id: int, channel_id: int, user_text: str) -> str:
    """Run one Claude turn, executing tool calls until the model produces a text reply."""
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(ZoneInfo("America/Los_Angeles"))
    stamp = (
        f"[utc={now_utc.isoformat(timespec='seconds')} | "
        f"local={now_local.strftime('%a %Y-%m-%d %H:%M %Z')}]"
    )
    user_block = [{"type": "text", "text": f"{stamp} {user_text}"}]
    messages = _load_history(thread_key) + [{"role": "user", "content": user_block}]
    _save_turn(thread_key, "user", user_block)

    final_text = ""
    for _ in range(8):  # safety bound on tool-use loop
        resp = await _client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        _save_turn(thread_key, "assistant", [b.model_dump() for b in resp.content])

        if resp.stop_reason != "tool_use":
            final_text = "".join(b.text for b in resp.content if b.type == "text").strip()
            break

        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            handler = _TOOL_DISPATCH.get(block.name)
            if handler is None:
                result = {"error": f"unknown tool: {block.name}"}
            else:
                log.info("tool.call", tool=block.name, input=block.input)
                try:
                    result = await handler(
                        user_id=user_id, channel_id=channel_id, **block.input
                    )
                    log.info(
                        "tool.result",
                        tool=block.name,
                        result_preview=json.dumps(result)[:300],
                    )
                except Exception as exc:  # surface to the model
                    log.exception("tool.failed", tool=block.name)
                    result = {"error": str(exc)}
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                }
            )
        user_block = tool_results
        messages.append({"role": "user", "content": user_block})
        _save_turn(thread_key, "user", user_block)

    return final_text
