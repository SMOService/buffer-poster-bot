"""Buffer Poster Bot — entry point.

Личный single-user TG-бот: пересылаешь пост → автопостинг в Buffer (X / LinkedIn / Threads
/ Bluesky / Mastodon) и в очередь Binance Square с random scheduling.

Структура:
- config.py           env + constants
- db.py               sqlite + миграции (SCHEMA_VERSION)
- bot_instance.py     aiogram Bot/Dispatcher singletons
- services/           buffer, binance, uploader
- handlers/           menu, channels, queue, binance, logs, post
- scheduler.py        background Binance publisher
"""

from __future__ import annotations

import asyncio

from aiogram.types import BotCommand

from bot_instance import bot, dp
from config import BINANCE_API_KEY, logger
from db import get_binance_queue_stats, init_db, save_channels
from scheduler import binance_scheduler
from services.buffer import fetch_channels

# import handlers for side-effect (декораторы регистрируются)
from handlers import binance as _h_binance  # noqa: F401
from handlers import channels as _h_channels  # noqa: F401
from handlers import logs as _h_logs  # noqa: F401
from handlers import menu as _h_menu  # noqa: F401
from handlers import post as _h_post  # noqa: F401
from handlers import queue as _h_queue  # noqa: F401


COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="channels", description="Каналы Buffer"),
    BotCommand(command="queue", description="Очереди (сводка)"),
    BotCommand(command="binance", description="Очередь Binance Square"),
    BotCommand(command="logs", description="Журнал публикаций"),
]


async def main():
    init_db()

    logger.info("Loading Buffer channels...")
    channels = await fetch_channels()
    if channels:
        save_channels(channels)
        logger.info("Loaded %d channels", len(channels))
    else:
        logger.warning("Could not load channels from Buffer")

    if BINANCE_API_KEY:
        stats = get_binance_queue_stats()
        logger.info("Binance Square: %d post(s) pending", stats["total"])
        asyncio.create_task(binance_scheduler())
        logger.info("Binance scheduler started")
    else:
        logger.info("BINANCE_SQUARE_API_KEY not set — Binance disabled")

    try:
        await bot.set_my_commands(COMMANDS)
    except Exception as e:
        logger.warning("set_my_commands failed: %s", e)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
