from __future__ import annotations

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot_instance import dp
from config import SERVICE_EMOJI
from db import get_binance_queue_stats, get_enabled_channels
from handlers.common import fmt_ts, is_me, is_me_cb
from keyboards import queue_kb
from services.buffer import count_scheduled_posts


async def render_queue() -> str:
    channels = get_enabled_channels()
    buffer_lines: list[str] = []
    if channels:
        for ch in channels:
            count = await count_scheduled_posts(ch["id"])
            count_str = "?" if count is None else str(count)
            buffer_lines.append(
                f"{SERVICE_EMOJI.get(ch['service'], '•')} <b>{ch['name']}</b>: {count_str} постов"
            )
    stats = get_binance_queue_stats()
    next_line = f"\n  следующий: {fmt_ts(stats['next_at'])}" if stats["next_at"] else ""
    parts = ["<b>📋 Очередь</b>\n"]
    parts.append("<b>Buffer:</b>")
    if buffer_lines:
        parts.extend(buffer_lines)
    else:
        parts.append("  нет активных каналов")
    parts.append("")
    parts.append(f"<b>Binance Square:</b> {stats['total']} постов{next_line}")
    parts.append(f"  опубликовано за 24ч: {stats['published_24h']}")
    return "\n".join(parts)


@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    if not is_me(message):
        return
    text = await render_queue()
    await message.answer(text, reply_markup=queue_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "menu:queue")
async def cb_queue(call: CallbackQuery):
    if not is_me_cb(call):
        return
    await call.answer()
    text = await render_queue()
    try:
        await call.message.edit_text(text, reply_markup=queue_kb(), parse_mode="HTML")
    except Exception:
        await call.message.answer(text, reply_markup=queue_kb(), parse_mode="HTML")
