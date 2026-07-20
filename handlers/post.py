from __future__ import annotations

import asyncio
import sys

import aiohttp
from aiogram import F
from aiogram.types import Message

from bot_instance import bot, dp
from config import BINANCE_API_KEY, BOT_TOKEN, SERVICE_EMOJI, logger
from db import (
    add_to_binance_queue,
    get_enabled_channels,
    is_duplicate,
    log_history,
    save_hash,
)
from handlers.common import fmt_due_iso, is_me, preview, random_due_at_iso
from services.buffer import create_post as buffer_create_post
from services.uploader import upload_image

# media_group_id → {photos: [file_id], video: dict | None, text: str}
album_buffer: dict[str, dict] = {}

# Bot API getFile отдаёт файлы только до 20 MB — большее видео скачать не сможем.
TELEGRAM_FILE_LIMIT = 20 * 1024 * 1024


def _extract_video(message: Message) -> dict | None:
    """Достаёт видео из сообщения → {file_id, cover_file_id, duration} или None.

    Слишком большое для Bot API видео помечаем too_big — хэндлер скажет об этом
    сразу, вместо того чтобы очередь билась в getFile до dead-letter.
    """
    v = message.video
    if not v:
        return None
    if v.file_size and v.file_size > TELEGRAM_FILE_LIMIT:
        return {"too_big": True, "size": v.file_size}
    return {
        "file_id": v.file_id,
        "cover_file_id": v.thumbnail.file_id if v.thumbnail else None,
        "duration": int(v.duration or 0),
    }


def _fmt_duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


async def _download_telegram_file(file_id: str) -> bytes | None:
    try:
        file = await bot.get_file(file_id)
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}",
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                return await r.read()
    except Exception as e:
        logger.error("download file_id=%s failed: %s", file_id, e)
        return None


async def _process_post(message: Message, text: str, file_ids: list[str], video: dict | None = None):
    if text and is_duplicate(text):
        await message.answer(
            f"⚠️ <b>Дубликат!</b> Этот пост уже публиковался ранее.\n\n"
            f"<i>{preview(text)}</i>",
            parse_mode="HTML",
        )
        return

    if video and video.get("too_big"):
        size_mb = video["size"] / 1024 / 1024
        await message.answer(
            f"⚠️ <b>Видео {size_mb:.0f} MB — больше лимита Bot API (20 MB)</b>, "
            f"скачать его бот не сможет. Публикую пост без видео.",
            parse_mode="HTML",
        )
        video = None

    logger.info("process_post: %d photo(s), video=%s → uploading",
                len(file_ids), bool(video))
    sys.stderr.flush()

    image_urls: list[str] = []
    for idx, fid in enumerate(file_ids):
        data = await _download_telegram_file(fid)
        if data is None:
            continue
        url = await upload_image(data)
        if url:
            image_urls.append(url)
        else:
            logger.error("process_post: imgbb upload %d/%d failed", idx + 1, len(file_ids))

    if file_ids and not image_urls and not video:
        await message.answer("❌ Не удалось загрузить фото в imgbb.")
        return
    if not text and not image_urls and not video:
        return

    due_at = random_due_at_iso()
    result_lines: list[str] = []

    channels = get_enabled_channels()
    # Buffer видео не отправляем (нет проверенного video-asset flow, токен FORBIDDEN) —
    # и не создаём ПУСТОЙ пост, если кроме видео в сообщении ничего не было.
    if channels and (text or image_urls):
        success: list[dict] = []
        failed: list[tuple[dict, str]] = []
        for ch in channels:
            r = await buffer_create_post(ch["id"], text, image_urls, due_at)
            try:
                post_result = r["data"]["createPost"]
                if "post" in post_result:
                    success.append(ch)
                    log_history(
                        kind="buffer",
                        service=ch["service"],
                        channel_name=ch["name"],
                        status="success",
                        text_preview=preview(text),
                        ext_id=str(post_result["post"].get("id") or ""),
                    )
                else:
                    err = post_result.get("message") or "unknown"
                    failed.append((ch, err))
                    log_history(
                        kind="buffer",
                        service=ch["service"],
                        channel_name=ch["name"],
                        status="failed",
                        text_preview=preview(text),
                        error=err,
                    )
            except (KeyError, TypeError) as e:
                logger.error("Buffer unexpected %s: %s | %s", ch["id"], e, r)
                failed.append((ch, str(e)))
                log_history(
                    kind="buffer",
                    service=ch["service"],
                    channel_name=ch["name"],
                    status="failed",
                    text_preview=preview(text),
                    error=str(e),
                )
        result_lines.append(f"<b>Buffer</b> ⏰ {fmt_due_iso(due_at)}")
        for ch in success:
            result_lines.append(f"  ✅ {SERVICE_EMOJI.get(ch['service'], '•')} {ch['name']}")
        for ch, err in failed:
            result_lines.append(f"  ❌ {SERVICE_EMOJI.get(ch['service'], '•')} {ch['name']} — <i>{preview(err, 60)}</i>")

    if BINANCE_API_KEY and (text or video):
        if video:
            # contentType=3 — одно видео, imageList не поддерживается; фото уходят только в Buffer
            post_id = add_to_binance_queue(
                text,
                [],
                due_at,
                content_type=3,
                video_file_id=video["file_id"],
                video_cover_file_id=video.get("cover_file_id"),
                video_duration=video.get("duration"),
            )
            media_tag = f" (🎬 {_fmt_duration(video['duration'])})"
        else:
            post_id = add_to_binance_queue(
                text,
                image_urls,
                due_at,
                image_file_ids=file_ids,
            )
            media_tag = f" ({len(file_ids)} фото)" if file_ids else ""
        result_lines.append(f"\n<b>Binance Square</b> ⏰ {fmt_due_iso(due_at)}")
        result_lines.append(f"  📥 в очереди #{post_id}{media_tag}")
    elif BINANCE_API_KEY:
        result_lines.append("\n<b>Binance Square</b>: пропущен (нет текста)")

    if image_urls:
        result_lines.append(f"\n🖼 фото: {len(image_urls)} шт.")
    if video:
        result_lines.append(f"\n🎬 видео: {_fmt_duration(video['duration'])} → Binance Square (Buffer видео не поддерживает)")
    if text:
        result_lines.append(f"<i>{preview(text)}</i>")

    save_hash(text)
    await message.answer("\n".join(result_lines), parse_mode="HTML")


async def _process_album(media_group_id: str, message: Message):
    await asyncio.sleep(1.5)
    group = album_buffer.pop(media_group_id, None)
    if not group:
        return
    await _process_post(message, group["text"], group["photos"], group.get("video"))


@dp.message(F.photo | F.video | (F.text & ~F.text.startswith("/")))
async def handle_post(message: Message):
    if not is_me(message):
        return

    if message.media_group_id:
        mgid = message.media_group_id
        text = message.caption or ""
        file_id = message.photo[-1].file_id if message.photo else None
        video = _extract_video(message)

        if mgid not in album_buffer:
            album_buffer[mgid] = {"photos": [], "video": None, "text": text}
            asyncio.create_task(_process_album(mgid, message))

        if file_id:
            album_buffer[mgid]["photos"].append(file_id)
        if video and not album_buffer[mgid]["video"]:
            album_buffer[mgid]["video"] = video  # contentType=3 = одно видео, берём первое
        if text and not album_buffer[mgid]["text"]:
            album_buffer[mgid]["text"] = text
        return

    text = message.caption or message.text or ""
    file_ids: list[str] = []
    if message.photo:
        file_ids.append(message.photo[-1].file_id)

    await _process_post(message, text, file_ids, _extract_video(message))
