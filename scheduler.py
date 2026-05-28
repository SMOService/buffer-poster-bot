from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from bot_instance import bot, download_telegram_file
from config import ALLOWED_USER_ID, BINANCE_API_KEY, BINANCE_USE_IMAGES, logger
from db import (
    get_pending_binance_posts,
    is_binance_paused,
    log_history,
    mark_binance_failed,
    mark_binance_published,
)
from services.binance import publish_image_post, publish_text


def _preview(text: str, n: int = 80) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"


async def _build_image_payload(file_ids: list[str]) -> list[tuple[bytes, str]]:
    out: list[tuple[bytes, str]] = []
    for fid in file_ids[:4]:
        try:
            data, name = await download_telegram_file(fid)
            out.append((data, name))
        except Exception as e:
            logger.error("scheduler: failed to download file_id=%s: %s", fid, e)
    return out


async def _publish_one(post: dict):
    text = post["text"] or ""
    file_ids = post.get("image_file_ids") or []
    if file_ids and BINANCE_USE_IMAGES:
        payload = await _build_image_payload(file_ids)
        result = await publish_image_post(text, payload)
    else:
        result = await publish_text(text)

    if result.ok:
        mark_binance_published(post["id"])
        log_history(
            kind="binance",
            service="binance_square",
            status="success",
            text_preview=_preview(text),
            ext_id=result.post_id,
            ext_url=result.url,
        )
        message = (
            f"✅ <b>Binance Square опубликовано</b>\n\n"
            f"<i>{_preview(text)}</i>"
        )
        if result.url:
            message += f"\n{result.url}"
        try:
            await bot.send_message(ALLOWED_USER_ID, message, parse_mode="HTML")
        except Exception:
            pass
        logger.info("Binance scheduler: published post id=%d url=%s", post["id"], result.url or "(none)")
    else:
        mark_binance_failed(post["id"], result.error or "unknown")
        log_history(
            kind="binance",
            service="binance_square",
            status="failed",
            text_preview=_preview(text),
            error=result.error,
        )
        logger.error("Binance scheduler: failed post id=%d error=%s", post["id"], result.error)


async def binance_scheduler():
    while True:
        try:
            if is_binance_paused():
                await asyncio.sleep(60)
                continue
            pending = get_pending_binance_posts()
            if pending:
                logger.info("Binance scheduler: %d due", len(pending))
            for post in pending:
                if is_binance_paused():
                    break
                await _publish_one(post)
        except Exception as e:
            logger.error("Binance scheduler tick error: %s", e)
        await asyncio.sleep(60)


def has_binance_key() -> bool:
    return bool(BINANCE_API_KEY)


def utcnow_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())
