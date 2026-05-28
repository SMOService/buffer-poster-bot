from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import SERVICE_EMOJI


def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu() -> InlineKeyboardMarkup:
    return kb([
        [
            InlineKeyboardButton(text="📡 Каналы", callback_data="menu:channels"),
            InlineKeyboardButton(text="📋 Очередь", callback_data="menu:queue"),
        ],
        [
            InlineKeyboardButton(text="🪙 Binance Square", callback_data="menu:binance:0"),
            InlineKeyboardButton(text="📊 Логи", callback_data="menu:logs:0:all"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
            InlineKeyboardButton(text="🔁 Обновить", callback_data="menu:home"),
        ],
    ])


def back_to_menu_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="← Главное меню", callback_data="menu:home")]


def channels_kb(all_ch: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ch in all_ch:
        tick = "✅" if ch["enabled"] else "☑️"
        label = f"{tick} {SERVICE_EMOJI.get(ch['service'], '•')} {ch['name']} ({ch['service']})"
        action = "off" if ch["enabled"] else "on"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"ch:{action}:{ch['id']}")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить из Buffer", callback_data="ch:refresh")])
    rows.append(back_to_menu_row())
    return kb(rows)


def queue_kb() -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton(text="🔁 Обновить", callback_data="menu:queue")],
        back_to_menu_row(),
    ])


def binance_list_kb(rows: list[dict], page: int, total_pages: int, paused: bool) -> InlineKeyboardMarkup:
    kb_rows: list[list[InlineKeyboardButton]] = []
    for r in rows:
        kb_rows.append([
            InlineKeyboardButton(text=f"#{r['id']} • открыть", callback_data=f"bq:open:{r['id']}:{page}"),
        ])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹ Назад", callback_data=f"menu:binance:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{max(total_pages,1)}", callback_data="bq:noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="Вперёд ›", callback_data=f"menu:binance:{page+1}"))
    if nav:
        kb_rows.append(nav)
    toggle_label = "▶️ Возобновить" if paused else "⏸ Пауза"
    kb_rows.append([
        InlineKeyboardButton(text=toggle_label, callback_data="bq:toggle_pause"),
        InlineKeyboardButton(text="⚡ Опубликовать все", callback_data="bq:flush_all"),
    ])
    kb_rows.append(back_to_menu_row())
    return kb(kb_rows)


def binance_post_kb(post_id: int, page: int) -> InlineKeyboardMarkup:
    return kb([
        [
            InlineKeyboardButton(text="📤 Отправить сейчас", callback_data=f"bq:send:{post_id}:{page}"),
            InlineKeyboardButton(text="🔁 Перепланировать", callback_data=f"bq:reschedule:{post_id}:{page}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"bq:edit:{post_id}:{page}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"bq:delete:{post_id}:{page}"),
        ],
        [InlineKeyboardButton(text="‹ К очереди", callback_data=f"menu:binance:{page}")],
        back_to_menu_row(),
    ])


def edit_cancel_kb(post_id: int, page: int) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton(text="✖️ Отменить", callback_data=f"bq:open:{post_id}:{page}")],
    ])


def confirm_flush_kb() -> InlineKeyboardMarkup:
    return kb([
        [
            InlineKeyboardButton(text="✅ Да, отправить все", callback_data="bq:flush_all_confirm"),
            InlineKeyboardButton(text="✖️ Отмена", callback_data="menu:binance:0"),
        ],
    ])


def confirm_delete_kb(post_id: int, page: int) -> InlineKeyboardMarkup:
    return kb([
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"bq:delete_confirm:{post_id}:{page}"),
            InlineKeyboardButton(text="✖️ Отмена", callback_data=f"bq:open:{post_id}:{page}"),
        ],
    ])


def logs_kb(page: int, total_pages: int, filt: str) -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹ Назад", callback_data=f"menu:logs:{page-1}:{filt}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{max(total_pages,1)}", callback_data="bq:noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="Вперёд ›", callback_data=f"menu:logs:{page+1}:{filt}"))

    next_filter = "fail" if filt == "all" else "all"
    filter_label = "🔴 Только фейлы" if filt == "all" else "🟢 Все"

    rows: list[list[InlineKeyboardButton]] = []
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(text=filter_label, callback_data=f"menu:logs:0:{next_filter}"),
        InlineKeyboardButton(text="🔁 Обновить", callback_data=f"menu:logs:{page}:{filt}"),
    ])
    rows.append(back_to_menu_row())
    return kb(rows)


def settings_kb(paused: bool) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton(
            text=f"Binance scheduler: {'⏸ на паузе' if paused else '▶️ активен'}",
            callback_data="bq:toggle_pause",
        )],
        back_to_menu_row(),
    ])
