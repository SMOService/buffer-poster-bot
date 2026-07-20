from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from bot_instance import bot, download_telegram_file
from config import (
    ALLOWED_USER_ID,
    BINANCE_API_KEY,
    BINANCE_BACKOFF_BASE_SEC,
    BINANCE_BACKOFF_MAX_SEC,
    BINANCE_MAX_ATTEMPTS,
    BINANCE_PUBLISHED_TTL_DAYS,
    BINANCE_USE_IMAGES,
    logger,
)
from db import (
    claim_binance_post,
    cleanup_published_binance,
    defer_pending_binance,
    get_binance_quota_hold,
    get_pending_binance_posts,
    is_binance_paused,
    log_history,
    mark_binance_dead,
    mark_binance_published,
    mark_binance_retry,
    release_binance_post,
    set_binance_quota_hold,
)
from services.binance import publish_image_post, publish_text, publish_video_post


def _preview(text: str, n: int = 80) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"


def _utcnow() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _next_utc_midnight() -> int:
    """Binance дневные лимиты сбрасываются в 00:00 UTC."""
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(nxt.timestamp())


def _backoff_delay(attempts_done: int) -> int:
    """attempts_done — число уже выполненных попыток (после инкремента). 5м→×2→cap 6ч."""
    factor = 2 ** max(0, attempts_done - 1)
    return min(BINANCE_BACKOFF_MAX_SEC, BINANCE_BACKOFF_BASE_SEC * factor)


async def _notify(text: str):
    try:
        await bot.send_message(ALLOWED_USER_ID, text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass


async def _build_image_payload(file_ids: list[str]) -> list[tuple[bytes, str]]:
    out: list[tuple[bytes, str]] = []
    for fid in file_ids[:4]:
        try:
            data, name = await download_telegram_file(fid)
            out.append((data, name))
        except Exception as e:
            logger.error("scheduler: failed to download file_id=%s: %s", fid, e)
    return out


async def _publish_one(post: dict) -> str:
    """Публикует один пост. Возвращает исход: ok|dead|retry|quota|auth|skip.

    Перед работой атомарно захватывает пост (pending → publishing) — scheduler-тик
    и ручное «Отправить сейчас» не могут опубликовать один пост дважды.
    """
    if not claim_binance_post(post["id"]):
        logger.info("Binance scheduler: skip id=%d (уже публикуется/не pending)", post["id"])
        return "skip"
    text = post["text"] or ""
    file_ids = post.get("image_file_ids") or []
    video_file_id = post.get("video_file_id")
    wants_images = bool(file_ids) and BINANCE_USE_IMAGES

    if video_file_id:
        try:
            video = await download_telegram_file(video_file_id)
        except Exception as e:
            # Telegram не отдал видео — не теряем: backoff-ретрай
            return await _handle_retry(post, f"не удалось скачать видео из Telegram: {e}")
        cover = None
        cover_fid = post.get("video_cover_file_id")
        if cover_fid:
            try:
                cover = await download_telegram_file(cover_fid)
            except Exception as e:
                logger.warning("scheduler: video cover download failed id=%d: %s", post["id"], e)
        result = await publish_video_post(text, video, float(post.get("video_duration") or 0), cover)
    elif wants_images:
        payload = await _build_image_payload(file_ids)
        if not payload:
            # Telegram не отдал ни одной картинки — не теряем: backoff-ретрай
            return await _handle_retry(post, "не удалось скачать фото из Telegram")
        result = await publish_image_post(text, payload)
    else:
        result = await publish_text(text)

    if result.ok:
        mark_binance_published(post["id"])
        log_history(
            kind="binance", service="binance_square", status="success",
            text_preview=_preview(text), ext_id=result.post_id, ext_url=result.url,
        )
        if result.video_attached:
            img = " (🎬 видео)"
        elif result.images_attached:
            img = f" (🖼 {result.images_attached} фото)"
        else:
            img = ""
        msg = f"✅ <b>Binance Square опубликовано</b>{img}\n\n<i>{_preview(text)}</i>"
        if result.url:
            msg += f"\n{result.url}"
        await _notify(msg)
        logger.info("Binance scheduler: published id=%d imgs=%d url=%s",
                    post["id"], result.images_attached, result.url or "(none)")
        return "ok"

    error = result.error or "unknown"
    kind = result.kind or "transient"
    log_history(
        kind="binance", service="binance_square", status="failed",
        text_preview=_preview(text), error=f"[{kind}] {error}",
    )

    if kind == "quota":
        return await _handle_quota(post, error)
    if kind == "auth":
        return await _handle_auth(post, error)
    if kind == "permanent":
        mark_binance_dead(post["id"], error)
        await _notify(
            f"⛔ <b>Binance отклонил пост #{post['id']}</b>\n"
            f"Причина: <code>{_preview(error, 120)}</code>\n"
            f"Ретраи остановлены. Отредактируй текст (/binance → #{post['id']}) или удали."
        )
        logger.error("Binance scheduler: DEAD id=%d (permanent) error=%s", post["id"], error)
        return "dead"
    return await _handle_retry(post, error)


async def _handle_retry(post: dict, error: str) -> str:
    attempts = int(post.get("attempt_count") or 0) + 1
    if attempts >= BINANCE_MAX_ATTEMPTS:
        mark_binance_dead(post["id"], error)
        await _notify(
            f"⛔ <b>Binance пост #{post['id']} снят</b> — исчерпаны попытки ({attempts}).\n"
            f"Последняя ошибка: <code>{_preview(error, 120)}</code>\n"
            f"Открой /binance → #{post['id']} чтобы переотправить вручную или удалить."
        )
        logger.error("Binance scheduler: DEAD id=%d (max attempts) error=%s", post["id"], error)
        return "dead"
    delay = _backoff_delay(attempts)
    mark_binance_retry(post["id"], error, _utcnow() + delay)
    logger.warning("Binance scheduler: retry id=%d attempt=%d in %ds error=%s",
                   post["id"], attempts, delay, error)
    return "retry"


async def _handle_quota(post: dict, error: str) -> str:
    until = _next_utc_midnight()
    set_binance_quota_hold(until)
    release_binance_post(post["id"], until)  # текущий пост: publishing → pending до сброса
    moved = defer_pending_binance(until)
    when = datetime.fromtimestamp(until, tz=timezone.utc).strftime("%d %b %H:%M UTC")
    await _notify(
        f"⏳ <b>Binance: дневной лимит исчерпан</b>\n"
        f"<code>{_preview(error, 100)}</code>\n"
        f"Публикация на паузе до {when} (сброс квоты). "
        f"Отложено постов: {moved}. Картинки сохранены — уйдут после сброса."
    )
    logger.warning("Binance scheduler: QUOTA hold until %d, deferred %d posts", until, moved)
    return "quota"


async def _handle_auth(post: dict, error: str) -> str:
    # ключ невалиден — глобальная проблема, нет смысла молотить; пауза на 1ч + один пуш
    until = _utcnow() + 3600
    set_binance_quota_hold(until)
    release_binance_post(post["id"], until)  # текущий пост: publishing → pending
    defer_pending_binance(until)
    await _notify(
        f"🔑 <b>Binance: проблема с API-ключом</b>\n"
        f"<code>{_preview(error, 100)}</code>\n"
        f"Публикация на паузе на 1ч. Проверь BINANCE_SQUARE_API_KEY в ENV (Coolify)."
    )
    logger.error("Binance scheduler: AUTH error=%s, hold 1h", error)
    return "auth"


_CLEANUP_INTERVAL = 86400
_last_cleanup_at = 0


def _maybe_cleanup_published():
    global _last_cleanup_at
    now = _utcnow()
    if now - _last_cleanup_at < _CLEANUP_INTERVAL:
        return
    _last_cleanup_at = now
    try:
        n = cleanup_published_binance(BINANCE_PUBLISHED_TTL_DAYS)
        if n:
            logger.info("Binance scheduler: cleaned %d published posts older than %dd",
                        n, BINANCE_PUBLISHED_TTL_DAYS)
    except Exception as e:
        logger.error("Binance scheduler: published cleanup failed: %s", e)


async def binance_scheduler():
    while True:
        try:
            _maybe_cleanup_published()
            if is_binance_paused():
                await asyncio.sleep(60)
                continue
            hold = get_binance_quota_hold()
            if hold and _utcnow() < hold:
                await asyncio.sleep(60)
                continue
            pending = get_pending_binance_posts()
            if pending:
                logger.info("Binance scheduler: %d due", len(pending))
            for post in pending:
                if is_binance_paused():
                    break
                outcome = await _publish_one(post)
                if outcome in ("quota", "auth"):
                    break  # дальше в этом тике бессмысленно
                await asyncio.sleep(0.4)  # бережём rate-limit (POST 300ms+)
        except Exception as e:
            logger.error("Binance scheduler tick error: %s", e)
        await asyncio.sleep(60)


def has_binance_key() -> bool:
    return bool(BINANCE_API_KEY)


# Публичный алиас для ручной публикации из handlers (Send Now / Flush All) —
# единая kind-логика и уведомления.
publish_queued_post = _publish_one
