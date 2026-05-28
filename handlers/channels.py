from __future__ import annotations

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot_instance import dp
from db import (
    get_all_channels,
    save_channels,
    toggle_channel,
)
from handlers.common import is_me, is_me_cb
from keyboards import channels_kb
from services.buffer import fetch_channels


def render_channels(all_ch: list[dict]) -> str:
    enabled = sum(1 for c in all_ch if c["enabled"])
    return (
        f"<b>📡 Каналы ({enabled}/{len(all_ch)} активных)</b>\n\n"
        f"Нажми для включения/выключения. Пост форвардится только в активные."
    )


async def open_channels(call_or_msg, edit: bool):
    all_ch = get_all_channels()
    if not all_ch:
        text = "Каналы не найдены. Попробуй <code>🔄 Обновить из Buffer</code> или перезапусти бота."
        kb = channels_kb([])
    else:
        text = render_channels(all_ch)
        kb = channels_kb(all_ch)
    if edit:
        try:
            await call_or_msg.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
        await call_or_msg.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await call_or_msg.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(Command("channels"))
async def cmd_channels(message: Message):
    if not is_me(message):
        return
    await open_channels(message, edit=False)


@dp.callback_query(F.data == "menu:channels")
async def cb_channels(call: CallbackQuery):
    if not is_me_cb(call):
        return
    await call.answer()
    await open_channels(call, edit=True)


@dp.callback_query(F.data.startswith("ch:"))
async def cb_toggle(call: CallbackQuery):
    if not is_me_cb(call):
        return
    parts = call.data.split(":", 2)
    if len(parts) < 2:
        return await call.answer()
    action = parts[1]
    if action == "refresh":
        channels = await fetch_channels()
        if channels:
            save_channels(channels)
            await call.answer(f"Обновлено: {len(channels)} каналов")
        else:
            await call.answer("Не получилось обновить", show_alert=True)
    else:
        channel_id = parts[2] if len(parts) > 2 else ""
        if channel_id:
            toggle_channel(channel_id, action == "on")
        await call.answer("Сохранено")
    await open_channels(call, edit=True)
