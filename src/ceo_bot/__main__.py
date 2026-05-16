from __future__ import annotations

import asyncio
import logging

import structlog

from ceo_bot.bot import build_bot
from ceo_bot.config import settings
from ceo_bot.db import init_db
from ceo_bot.scheduler import start_scheduler


def _configure_logging() -> None:
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )


async def _run() -> None:
    _configure_logging()
    log = structlog.get_logger()
    log.info("startup", model=settings.anthropic_model, db=str(settings.database_path))

    init_db()
    bot = build_bot()
    scheduler = start_scheduler(bot)

    try:
        await bot.start(settings.discord_bot_token)
    finally:
        scheduler.shutdown(wait=False)
        await bot.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
