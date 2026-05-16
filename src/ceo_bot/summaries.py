"""Weekly summary job: Sunday 8 PM America/Los_Angeles, posted to whichever
channel had the most messages over the past 7 days."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import discord
import structlog
from anthropic import AsyncAnthropic

from ceo_bot.config import settings
from ceo_bot.db import cursor
from ceo_bot.tools.stats import compute_stats

log = structlog.get_logger()

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

WEEKLY_PROMPT = """You are writing a short weekly recap for Daniel and his wife.

Stats for the last 7 days:
{stats}

Notable attachments described (sample):
{attachments}

Links shared this week (sample):
{links}

Pending reminders set for the coming week:
{reminders}

Compose a friendly recap, max ~12 lines. Cover:
- Headline volume (messages, photos, links) in one sentence
- 2-4 themes or notable conversations from the week (infer from the content
  hints; be specific, not vague)
- Standout shared content (a memorable photo or article) if any
- What's queued up next week
- A one-line "vibe of the week"

Skip section headers — write it as a short, warm message. No emoji unless
they naturally fit. Don't editorialize about the relationship."""


def _most_active_channel(start: datetime) -> int | None:
    with cursor() as cur:
        row = cur.execute(
            """
            SELECT channel_id, COUNT(*) AS n
            FROM messages
            WHERE created_at >= ?
            GROUP BY channel_id
            ORDER BY n DESC
            LIMIT 1
            """,
            (start.isoformat(),),
        ).fetchone()
    return row["channel_id"] if row else None


def _attachment_samples(start: datetime, limit: int = 8) -> list[str]:
    with cursor() as cur:
        rows = cur.execute(
            """
            SELECT a.filename, a.content_type, coalesce(a.summary, a.ocr_text, '') AS desc
            FROM attachments a
            WHERE a.created_at >= ? AND (a.summary IS NOT NULL OR a.ocr_text IS NOT NULL)
            ORDER BY a.id DESC LIMIT ?
            """,
            (start.isoformat(), limit),
        ).fetchall()
    return [f"- {r['filename']} ({r['content_type']}): {r['desc'][:300]}" for r in rows]


def _link_samples(start: datetime, limit: int = 8) -> list[str]:
    with cursor() as cur:
        rows = cur.execute(
            """
            SELECT url, title, summary FROM link_summaries
            WHERE created_at >= ? AND summary IS NOT NULL
            ORDER BY id DESC LIMIT ?
            """,
            (start.isoformat(), limit),
        ).fetchall()
    return [
        f"- {r['url']}  {('— ' + r['title']) if r['title'] else ''}\n  {r['summary'][:300]}"
        for r in rows
    ]


def _upcoming_reminders(now: datetime, horizon: timedelta = timedelta(days=7)) -> list[str]:
    with cursor() as cur:
        rows = cur.execute(
            """
            SELECT due_at, payload FROM reminders
            WHERE status = 'pending' AND due_at BETWEEN ? AND ?
            ORDER BY due_at
            """,
            (now.isoformat(), (now + horizon).isoformat()),
        ).fetchall()
    return [f"- {r['due_at']}: {r['payload']}" for r in rows]


def _format(items: list[str], empty: str = "(none)") -> str:
    return "\n".join(items) if items else empty


async def run_weekly_summary(bot: discord.Client) -> None:
    now = datetime.now(UTC)
    start = now - timedelta(days=7)
    log.info("summary.start", window_start=start.isoformat(), window_end=now.isoformat())

    channel_id = _most_active_channel(start)
    if channel_id is None:
        log.info("summary.no_activity")
        return

    stats = compute_stats("7d")
    if stats.get("messages_total", 0) == 0:
        log.info("summary.no_messages")
        return

    prompt = WEEKLY_PROMPT.format(
        stats=stats,
        attachments=_format(_attachment_samples(start)),
        links=_format(_link_samples(start)),
        reminders=_format(_upcoming_reminders(now)),
    )

    resp = await _client.messages.create(
        model=settings.anthropic_model,
        max_tokens=900,
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        log.warning("summary.empty_response")
        return

    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    try:
        await channel.send(f"**Weekly recap**\n{text}")
        log.info("summary.posted", channel_id=channel_id, chars=len(text))
    except discord.HTTPException as exc:
        log.warning("summary.send_failed", channel_id=channel_id, error=str(exc))
