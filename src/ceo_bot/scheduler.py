from __future__ import annotations

import json
from datetime import UTC, datetime

import discord
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ceo_bot.backups import run_weekly_backup
from ceo_bot.db import cursor
from ceo_bot.summaries import run_weekly_summary

log = structlog.get_logger()


async def _dispatch_due_reminders(bot: discord.Client) -> None:
    now = datetime.now(UTC).isoformat()
    with cursor() as cur:
        due = cur.execute(
            "SELECT id, channel_id, user_ids, payload FROM reminders WHERE status='pending' AND due_at <= ?",
            (now,),
        ).fetchall()
        for row in due:
            channel = bot.get_channel(row["channel_id"])
            if channel is None:
                log.warning("reminder.no_channel", channel_id=row["channel_id"], reminder_id=row["id"])
                continue
            mentions = " ".join(f"<@{uid}>" for uid in json.loads(row["user_ids"]))
            text = f"{mentions} reminder: {row['payload']}".strip()
            try:
                await channel.send(text)
            except discord.HTTPException as exc:
                log.warning("reminder.send_failed", id=row["id"], error=str(exc))
                continue
            cur.execute(
                "UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), row["id"]),
            )


def start_scheduler(bot: discord.Client) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(_dispatch_due_reminders, "interval", seconds=30, args=[bot])
    sched.add_job(run_weekly_backup, "cron", day_of_week="sun", hour=2, minute=0)
    sched.add_job(
        run_weekly_summary,
        "cron",
        day_of_week="sun",
        hour=20,
        minute=0,
        timezone="America/Los_Angeles",
        args=[bot],
    )
    sched.start()
    return sched
