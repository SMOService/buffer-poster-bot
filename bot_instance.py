from __future__ import annotations

"""Aiogram Bot/Dispatcher singletons, imported by handlers and scheduler.

Кладём отдельным модулем чтобы избежать циклических импортов между
bot.py (entry point) и handlers/services.
"""

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


async def download_telegram_file(file_id: str) -> tuple[bytes, str]:
    """Download bytes by Telegram file_id. Returns (bytes, suggested_name)."""
    file = await bot.get_file(file_id)
    file_path = file.file_path
    name = file_path.rsplit("/", 1)[-1] if file_path else f"{file_id}.jpg"
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}",
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            data = await r.read()
    return data, name
