from __future__ import annotations

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot_instance import dp
from config import (
    BINANCE_API_KEY,
    SCHEDULE_MAX_HOURS,
    SCHEDULE_MIN_HOURS,
    SERVICE_EMOJI,
)
from db import (
    get_binance_queue_stats,
    get_enabled_channels,
    history_stats,
    is_binance_paused,
)
from handlers.common import fmt_ts, is_me, is_me_cb
from keyboards import main_menu, settings_kb


def render_home() -> str:
    channels = get_enabled_channels()
    ch_lines = "\n".join(
        f"  {SERVICE_EMOJI.get(c['service'], '•')} {c['name']} ({c['service']})"
        for c in channels
    ) or "  нет активных каналов"

    if not BINANCE_API_KEY:
        binance_status = "❌ не настроен"
    elif is_binance_paused():
        binance_status = "⏸ на паузе"
    else:
        binance_status = "✅ активен"

    stats = get_binance_queue_stats()
    next_line = f"\n  следующий: {fmt_ts(stats['next_at'])}" if stats["next_at"] else ""

    hs = history_stats()
    hist_line = f"📊 история: {hs['ok']} ✅ / {hs['fail']} ❌ (всего {hs['total']})"

    return (
        f"👋 <b>Buffer Poster Bot</b>\n\n"
        f"<b>Активных каналов Buffer ({len(channels)}):</b>\n{ch_lines}\n\n"
        f"<b>Binance Square:</b> {binance_status}\n"
        f"  в очереди: {stats['total']} постов{next_line}\n\n"
        f"<b>Расписание:</b> случайно {int(SCHEDULE_MIN_HOURS)}–{int(SCHEDULE_MAX_HOURS)} ч\n"
        f"{hist_line}\n\n"
        f"Пересылай посты — уйдут в Buffer и Binance Square автоматически."
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not is_me(message):
        return
    await message.answer(render_home(), reply_markup=main_menu(), parse_mode="HTML")


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    if not is_me(message):
        return
    await message.answer(render_home(), reply_markup=main_menu(), parse_mode="HTML")


@dp.callback_query(F.data == "menu:home")
async def cb_home(call: CallbackQuery):
    if not is_me_cb(call):
        return
    await call.answer()
    try:
        await call.message.edit_text(render_home(), reply_markup=main_menu(), parse_mode="HTML")
    except Exception:
        await call.message.answer(render_home(), reply_markup=main_menu(), parse_mode="HTML")


def render_settings() -> str:
    paused = is_binance_paused()
    lines = ["<b>⚙️ Настройки</b>\n"]
    lines.append(
        f"Binance scheduler: <b>{'⏸ на паузе' if paused else '▶️ активен'}</b>"
    )
    lines.append(f"Расписание: случайно {int(SCHEDULE_MIN_HOURS)}–{int(SCHEDULE_MAX_HOURS)} ч (env)")
    lines.append(f"Binance Square key: {'✅ задан' if BINANCE_API_KEY else '❌ нет'}")
    return "\n".join(lines)


@dp.callback_query(F.data == "menu:settings")
async def cb_settings(call: CallbackQuery):
    if not is_me_cb(call):
        return
    await call.answer()
    await call.message.edit_text(
        render_settings(),
        reply_markup=settings_kb(is_binance_paused()),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "bq:noop")
async def cb_noop(call: CallbackQuery):
    if not is_me_cb(call):
        return
    await call.answer()
