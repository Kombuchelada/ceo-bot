from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import discord
import structlog

from ceo_bot import storage
from ceo_bot.claude import run_turn
from ceo_bot.db import cursor
from ceo_bot.enrichment import enrich_attachment

log = structlog.get_logger()


def should_respond(message: discord.Message, bot_user: discord.ClientUser | None) -> bool:
    """The bot listens to every message but only *responds* when addressed."""
    if bot_user is None:
        return False
    if isinstance(message.channel, discord.DMChannel):
        return True
    if bot_user in message.mentions:
        return True
    return False


async def archive_message(message: discord.Message) -> None:
    now = datetime.now(UTC).isoformat()
    enrich_jobs: list[tuple[int, str | None, bytes]] = []
    with cursor() as cur:
        cur.execute(
            """
            INSERT OR IGNORE INTO messages
                (id, channel_id, guild_id, author_id, author_name, content,
                 reply_to_id, created_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                message.channel.id,
                message.guild.id if message.guild else None,
                message.author.id,
                message.author.display_name,
                message.content,
                message.reference.message_id if message.reference else None,
                message.created_at.isoformat(),
                json.dumps({"flags": message.flags.value}),
            ),
        )

        for att in message.attachments:
            try:
                data = await att.read()
            except discord.HTTPException as exc:
                log.warning("attachment.read_failed", attachment=att.filename, error=str(exc))
                continue
            key = storage.object_key(message.id, att.filename)
            key, sha = storage.put_object(key, data, att.content_type)
            cur.execute(
                """
                INSERT INTO attachments
                    (message_id, filename, content_type, size_bytes, sha256, s3_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message.id, att.filename, att.content_type, att.size, sha, key, now),
            )
            enrich_jobs.append((cur.lastrowid, att.content_type, data))

    for attachment_id, ct, data in enrich_jobs:
        asyncio.create_task(enrich_attachment(attachment_id, ct, data))

    log.info(
        "message.archived",
        id=message.id,
        author=message.author.display_name,
        attachments=len(message.attachments),
    )


async def respond_with_claude(bot: discord.Client, message: discord.Message) -> None:
    async with message.channel.typing():
        reply = await run_turn(
            thread_key=str(message.channel.id),
            user_id=message.author.id,
            channel_id=message.channel.id,
            user_text=message.clean_content,
        )
    if reply:
        await message.reply(reply, mention_author=False)
