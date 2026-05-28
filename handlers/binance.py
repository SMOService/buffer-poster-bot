from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot_instance import dp
from config import BINANCE_USE_IMAGES, logger
from db import (
    delete_binance_post,
    get_binance_post,
    get_binance_queue_stats,
    is_binance_paused,
    list_binance_queue,
    log_history,
    mark_binance_failed,
    mark_binance_published,
    set_binance_paused,
    update_binance_due_at,
    update_binance_text,
)
from handlers.common import (
    fmt_delta,
    fmt_ts,
    is_me,
    is_me_cb,
    preview,
    random_due_at_iso,
)
from keyboards import (
    binance_list_kb,
    binance_post_kb,
    confirm_delete_kb,
    confirm_flush_kb,
    edit_cancel_kb,
)
from services.binance import publish_image_post, publish_text
from state import EditBinance

PAGE_SIZE = 8


def render_list(page: int) -> tuple[str, int]:
    offset = page * PAGE_SIZE
    rows = list_binance_queue(limit=PAGE_SIZE, offset=offset)
    stats = get_binance_queue_stats()
    total_pages = max(1, (stats["total"] + PAGE_SIZE - 1) // PAGE_SIZE)
    now = int(datetime.now(timezone.utc).timestamp())

    if not rows:
        body = "📭 Очередь Binance Square пуста."
    else:
        parts: list[str] = []
        for r in rows:
            overdue = r["publish_at"] < now
            icon = "🔴" if overdue else "⏰"
            tag = "🖼" if (r.get("image_file_ids") or r.get("image_urls")) else "📝"
            head = (
                f"<b>#{r['id']}</b> {icon} {fmt_ts(r['publish_at'])} "
                f"<i>({fmt_delta(r['publish_at'], now)})</i> {tag}"
            )
            tail = f"<i>{preview(r['text'], 100)}</i>"
            err = f"\n⚠️ <code>{preview(r['last_error'], 100)}</code>" if r.get("last_error") else ""
            parts.append(f"{head}\n{tail}{err}")
        body = "\n\n".join(parts)

    paused = is_binance_paused()
    status = "⏸ <b>scheduler на паузе</b>" if paused else "▶️ scheduler активен"
    header = (
        f"<b>🪙 Binance Square — очередь</b>\n"
        f"{status}\n"
        f"всего: {stats['total']} • опубликовано за 24ч: {stats['published_24h']}\n\n"
    )
    return header + body, total_pages


@dp.message(Command("binance"))
async def cmd_binance(message: Message):
    if not is_me(message):
        return
    text, total_pages = render_list(0)
    rows = list_binance_queue(limit=PAGE_SIZE, offset=0)
    await message.answer(
        text,
        reply_markup=binance_list_kb(rows, 0, total_pages, is_binance_paused()),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data.startswith("menu:binance:"))
async def cb_binance_list(call: CallbackQuery):
    if not is_me_cb(call):
        return
    parts = call.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    await call.answer()
    text, total_pages = render_list(page)
    rows = list_binance_queue(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    try:
        await call.message.edit_text(
            text,
            reply_markup=binance_list_kb(rows, page, total_pages, is_binance_paused()),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        await call.message.answer(
            text,
            reply_markup=binance_list_kb(rows, page, total_pages, is_binance_paused()),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def render_post_detail(post: dict) -> str:
    overdue = post["publish_at"] < int(datetime.now(timezone.utc).timestamp())
    icon = "🔴" if overdue else "⏰"
    tag_lines: list[str] = []
    file_ids = post.get("image_file_ids") or []
    urls = post.get("image_urls") or []
    if file_ids:
        tag_lines.append(f"🖼 фото в очереди: {len(file_ids)} (заберутся из Telegram при публикации)")
    elif urls:
        tag_lines.append(f"🖼 imgbb URLs: {len(urls)} (legacy — Telegram file_ids нет)")
    if post.get("last_error"):
        tag_lines.append(f"⚠️ предыдущая ошибка: <code>{preview(post['last_error'], 200)}</code>")
    if post.get("attempt_count"):
        tag_lines.append(f"🔁 попыток: {post['attempt_count']}")
    extra = ("\n" + "\n".join(tag_lines)) if tag_lines else ""
    return (
        f"<b>🪙 Пост #{post['id']}</b>\n"
        f"{icon} {fmt_ts(post['publish_at'])} <i>({fmt_delta(post['publish_at'])})</i>{extra}\n\n"
        f"<i>{post['text']}</i>"
    )


@dp.callback_query(F.data.startswith("bq:open:"))
async def cb_open(call: CallbackQuery):
    if not is_me_cb(call):
        return
    parts = call.data.split(":")
    post_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    post = get_binance_post(post_id)
    if not post:
        await call.answer("Пост не найден", show_alert=True)
        return
    await call.answer()
    text = render_post_detail(post)
    await call.message.edit_text(
        text,
        reply_markup=binance_post_kb(post_id, page),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data.startswith("bq:send:"))
async def cb_send(call: CallbackQuery):
    if not is_me_cb(call):
        return
    parts = call.data.split(":")
    post_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    post = get_binance_post(post_id)
    if not post:
        await call.answer("Пост не найден", show_alert=True)
        return
    if post["published"]:
        await call.answer("Уже опубликован", show_alert=True)
        return
    await call.answer("⏳ Публикую…")
    await _publish_post_now(post)
    text, total_pages = render_list(page)
    rows = list_binance_queue(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    try:
        await call.message.edit_text(
            text,
            reply_markup=binance_list_kb(rows, page, total_pages, is_binance_paused()),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        pass


async def _publish_post_now(post: dict):
    """Inline-публикация одного поста (используется и для Send Now, и для Flush All)."""
    from bot_instance import download_telegram_file  # local import to avoid cycle at module load

    text = post["text"] or ""
    file_ids = post.get("image_file_ids") or []
    image_payload: list[tuple[bytes, str]] = []
    if file_ids and BINANCE_USE_IMAGES:
        for fid in file_ids[:4]:
            try:
                data, name = await download_telegram_file(fid)
                image_payload.append((data, name))
            except Exception as e:
                logger.error("send_now: failed to download file_id=%s: %s", fid, e)

    if image_payload:
        result = await publish_image_post(text, image_payload)
    else:
        result = await publish_text(text)

    if result.ok:
        mark_binance_published(post["id"])
        log_history(
            kind="binance",
            service="binance_square",
            status="success",
            text_preview=preview(text),
            ext_id=result.post_id,
            ext_url=result.url,
        )
    else:
        mark_binance_failed(post["id"], result.error or "unknown")
        log_history(
            kind="binance",
            service="binance_square",
            status="failed",
            text_preview=preview(text),
            error=result.error,
        )


@dp.callback_query(F.data.startswith("bq:reschedule:"))
async def cb_reschedule(call: CallbackQuery):
    if not is_me_cb(call):
        return
    parts = call.data.split(":")
    post_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    post = get_binance_post(post_id)
    if not post:
        await call.answer("Пост не найден", show_alert=True)
        return
    iso = random_due_at_iso()
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    update_binance_due_at(post_id, int(dt.timestamp()))
    await call.answer(f"Новое время: {dt.strftime('%d %b %H:%M UTC')}")
    post = get_binance_post(post_id)
    await call.message.edit_text(
        render_post_detail(post),
        reply_markup=binance_post_kb(post_id, page),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data.startswith("bq:delete:"))
async def cb_delete_confirm(call: CallbackQuery):
    if not is_me_cb(call):
        return
    parts = call.data.split(":")
    post_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    post = get_binance_post(post_id)
    if not post:
        await call.answer("Пост не найден", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        f"🗑 Удалить пост <b>#{post_id}</b>?\n\n<i>{preview(post['text'], 200)}</i>",
        reply_markup=confirm_delete_kb(post_id, page),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("bq:delete_confirm:"))
async def cb_delete(call: CallbackQuery):
    if not is_me_cb(call):
        return
    parts = call.data.split(":")
    post_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    delete_binance_post(post_id)
    await call.answer("Удалено")
    text, total_pages = render_list(page)
    rows = list_binance_queue(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    await call.message.edit_text(
        text,
        reply_markup=binance_list_kb(rows, page, total_pages, is_binance_paused()),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data.startswith("bq:edit:"))
async def cb_edit_request(call: CallbackQuery, state: FSMContext):
    if not is_me_cb(call):
        return
    parts = call.data.split(":")
    post_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    post = get_binance_post(post_id)
    if not post:
        await call.answer("Пост не найден", show_alert=True)
        return
    await state.set_state(EditBinance.waiting_text)
    await state.update_data(post_id=post_id, page=page)
    await call.answer()
    await call.message.edit_text(
        f"✏️ <b>Изменить пост #{post_id}</b>\n\n"
        f"Текущий текст:\n<i>{preview(post['text'], 500)}</i>\n\n"
        f"Пришли новый текст одним сообщением.",
        reply_markup=edit_cancel_kb(post_id, page),
        parse_mode="HTML",
    )


@dp.message(EditBinance.waiting_text, F.text)
async def cb_edit_apply(message: Message, state: FSMContext):
    if not is_me(message):
        return
    data = await state.get_data()
    post_id = int(data.get("post_id", 0))
    page = int(data.get("page", 0))
    await state.clear()
    if not post_id:
        return await message.answer("Не нашёл контекст редактирования.")
    new_text = (message.text or "").strip()
    if not new_text:
        return await message.answer("Текст пустой — отменено.")
    ok = update_binance_text(post_id, new_text)
    if not ok:
        return await message.answer(f"❌ Пост #{post_id} не найден или уже опубликован.")
    post = get_binance_post(post_id)
    await message.answer(
        f"✅ Пост <b>#{post_id}</b> обновлён.\n\n" + render_post_detail(post),
        reply_markup=binance_post_kb(post_id, page),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data == "bq:toggle_pause")
async def cb_toggle_pause(call: CallbackQuery):
    if not is_me_cb(call):
        return
    new_state = not is_binance_paused()
    set_binance_paused(new_state)
    await call.answer("⏸ Поставил на паузу" if new_state else "▶️ Возобновил")
    text, total_pages = render_list(0)
    rows = list_binance_queue(limit=PAGE_SIZE, offset=0)
    try:
        await call.message.edit_text(
            text,
            reply_markup=binance_list_kb(rows, 0, total_pages, new_state),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        pass


@dp.callback_query(F.data == "bq:flush_all")
async def cb_flush_confirm(call: CallbackQuery):
    if not is_me_cb(call):
        return
    pending_total = get_binance_queue_stats()["total"]
    if pending_total == 0:
        await call.answer("Очередь пуста", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        f"⚡ Опубликовать все <b>{pending_total}</b> постов прямо сейчас?\n\n"
        f"⚠️ Binance Square daily limit: 100 постов / 400 загрузок.",
        reply_markup=confirm_flush_kb(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "bq:flush_all_confirm")
async def cb_flush_run(call: CallbackQuery):
    if not is_me_cb(call):
        return
    await call.answer("⏳ Запускаю…")
    posts = list_binance_queue(limit=200, offset=0)
    if not posts:
        await call.message.edit_text("📭 Очередь пуста.", parse_mode="HTML")
        return
    ok, fail = 0, 0
    for p in posts:
        await _publish_post_now(p)
        post_after = get_binance_post(p["id"])
        if post_after and post_after["published"]:
            ok += 1
        else:
            fail += 1
    text, total_pages = render_list(0)
    rows = list_binance_queue(limit=PAGE_SIZE, offset=0)
    await call.message.edit_text(
        f"⚡ <b>Готово:</b> {ok} ✅ / {fail} ❌\n\n" + text,
        reply_markup=binance_list_kb(rows, 0, total_pages, is_binance_paused()),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
