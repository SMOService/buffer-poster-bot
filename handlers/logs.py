from __future__ import annotations

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot_instance import dp
from db import history_stats, list_history
from handlers.common import fmt_ts, is_me, is_me_cb, preview
from keyboards import back_to_menu_row, logs_kb

PAGE_SIZE = 8


def render_logs(page: int, filt: str) -> tuple[str, int]:
    offset = page * PAGE_SIZE
    rows = list_history(limit=PAGE_SIZE, offset=offset, only_failed=(filt == "fail"))
    hs = history_stats()
    total = hs["fail"] if filt == "fail" else hs["total"]
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    if not rows:
        body = "📭 Журнал пуст."
    else:
        lines: list[str] = []
        for r in rows:
            icon = "✅" if r["status"] == "success" else "❌"
            kind = r["kind"] or "?"
            service = r["service"] or ""
            chname = r["channel_name"] or ""
            head = f"{icon} <b>#{r['id']}</b> {fmt_ts(r['created_at'])}"
            tail = f"<i>{preview(r['text_preview'], 90)}</i>"
            meta = f"  └ <code>{kind}</code>"
            if service:
                meta += f" / {service}"
            if chname:
                meta += f" → {chname}"
            if r["ext_url"]:
                meta += f" • <a href=\"{r['ext_url']}\">link</a>"
            elif r["ext_id"]:
                meta += f" • id=<code>{r['ext_id']}</code>"
            if r["error"]:
                meta += f"\n  ⚠️ <code>{preview(r['error'], 120)}</code>"
            lines.append(f"{head}\n{tail}\n{meta}")
        body = "\n\n".join(lines)

    header = (
        f"<b>📊 Журнал</b> "
        f"<i>(всего {hs['total']}: {hs['ok']} ✅ / {hs['fail']} ❌"
        f"{', фильтр: фейлы' if filt == 'fail' else ''})</i>\n\n"
    )
    return header + body, total_pages


@dp.message(Command("logs"))
async def cmd_logs(message: Message):
    if not is_me(message):
        return
    text, total_pages = render_logs(0, "all")
    await message.answer(
        text,
        reply_markup=logs_kb(0, total_pages, "all"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data.startswith("menu:logs:"))
async def cb_logs(call: CallbackQuery):
    if not is_me_cb(call):
        return
    parts = call.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    filt = parts[3] if len(parts) > 3 else "all"
    if filt not in ("all", "fail"):
        filt = "all"
    await call.answer()
    text, total_pages = render_logs(page, filt)
    try:
        await call.message.edit_text(
            text,
            reply_markup=logs_kb(page, total_pages, filt),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        await call.message.answer(
            text,
            reply_markup=logs_kb(page, total_pages, filt),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
