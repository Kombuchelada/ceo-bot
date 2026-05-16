from __future__ import annotations

import discord
import structlog

from ceo_bot.config import settings
from ceo_bot.handlers.messages import archive_message, should_respond, respond_with_claude

log = structlog.get_logger()


def build_bot() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    intents.guilds = True
    intents.dm_messages = True

    bot = discord.Client(intents=intents)

    @bot.event
    async def on_ready() -> None:
        log.info("discord.ready", user=str(bot.user), guilds=len(bot.guilds))

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        if settings.allowed_user_ids and message.author.id not in settings.allowed_user_ids:
            return

        await archive_message(message)

        if should_respond(message, bot.user):
            await respond_with_claude(bot, message)

    return bot
