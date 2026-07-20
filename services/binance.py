"""Binance Square OpenAPI client.

Реализует официальный flow из binance/binance-skills-hub:
- v2 /image/presignedUrl  → PUT bytes → /image/imageStatus (polling)
- v1 /content/add         (contentType=1 short image post, contentType=2 article)

Replaces старую реализацию с одним только bodyTextOnly запросом.
"""

from __future__ import annotations

import asyncio
import mimetypes
from dataclasses import dataclass

import aiohttp

from config import (
    BINANCE_API_KEY,
    BINANCE_API_V1,
    BINANCE_API_V2,
    BINANCE_CLIENTTYPE,
    BINANCE_IMAGE_FALLBACK_TEXT,
    logger,
)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)
POLL_TIMEOUT = aiohttp.ClientTimeout(total=15)
UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=120)

_HEADERS = {
    "X-Square-OpenAPI-Key": BINANCE_API_KEY,
    "Content-Type": "application/json",
    "clienttype": BINANCE_CLIENTTYPE,
}

IMAGE_EXT_TO_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}

VIDEO_EXT_TO_MIME = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
}

# Известные коды ошибок (skill docs / Academy 2026-03 + наблюдённые в проде 2026-06).
ERROR_CODES = {
    "220003": "API key not found",
    "220004": "API key expired",
    "220009": "daily post limit exceeded (100/day)",
    "220014": "daily upload limit exceeded (400/day)",
    "220095": "coin pair count exceeds the allowed limit",
    "20002": "sensitive words detected",
    "20013": "content length limit exceeded",
    "20022": "sensitive words detected",
}

# Классификация для retry-политики.
#   permanent — контент отклонён, ретрай бессмысленен → dead-letter + уведомить
#   auth      — ключ невалиден/просрочен → стоп, уведомить (нужно чинить ENV)
#   quota     — дневной лимит Binance (сброс 00:00 UTC) → hold до сброса, пост сохранить
#   transient — сеть/таймаут/5xx/неизвестное → backoff-ретрай
PERMANENT_CODES = {"20002", "20013", "20022", "220095"}
AUTH_CODES = {"220003", "220004"}
QUOTA_CODES = {"220009", "220014"}

# Приоритет «решительности» при смешанных ошибках аплоада (выше = важнее).
_KIND_PRIORITY = {"transient": 1, "quota": 2, "permanent": 3, "auth": 4}


def classify_code(code: str | None) -> str:
    s = str(code) if code else ""
    if s in PERMANENT_CODES:
        return "permanent"
    if s in AUTH_CODES:
        return "auth"
    if s in QUOTA_CODES:
        return "quota"
    return "transient"


def _worse_kind(a: str | None, b: str) -> str:
    if a is None:
        return b
    return a if _KIND_PRIORITY.get(a, 1) >= _KIND_PRIORITY.get(b, 1) else b


class UploadError(RuntimeError):
    """Ошибка загрузки картинки с классом для retry-политики."""

    def __init__(self, message: str, *, kind: str = "transient", code: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.code = code


@dataclass
class BinanceResult:
    ok: bool
    post_id: str | None = None
    url: str | None = None
    error: str | None = None
    kind: str | None = None  # на неуспехе: permanent | auth | quota | transient
    images_attached: int = 0
    video_attached: bool = False
    raw: dict | None = None


def describe_error_code(code: str | None, message: str | None = None) -> str:
    if not code:
        return message or "unknown error"
    known = ERROR_CODES.get(str(code))
    if known:
        return f"{code}: {known}"
    if message:
        return f"{code}: {message}"
    return str(code)


async def _post_json(session: aiohttp.ClientSession, url: str, body: dict, *, timeout=DEFAULT_TIMEOUT) -> dict:
    async with session.post(url, json=body, headers=_HEADERS, timeout=timeout) as resp:
        text = await resp.text()
        try:
            data = await resp.json(content_type=None)
        except aiohttp.ContentTypeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["_http_status"] = resp.status
        if resp.status >= 400 and not data.get("code"):
            data["_raw_text"] = text[:500]
        return data


def _content_type_for(image_name: str) -> str:
    ext = image_name.rsplit(".", 1)[-1].lower() if "." in image_name else ""
    if ext in IMAGE_EXT_TO_MIME:
        return IMAGE_EXT_TO_MIME[ext]
    if ext in VIDEO_EXT_TO_MIME:
        return VIDEO_EXT_TO_MIME[ext]
    guess, _ = mimetypes.guess_type(image_name)
    return guess or "application/octet-stream"


async def _request_image_presigned(session: aiohttp.ClientSession, image_name: str) -> dict:
    return await _post_json(
        session,
        f"{BINANCE_API_V2}/image/presignedUrl",
        {"imageName": image_name},
    )


async def _put_bytes(session: aiohttp.ClientSession, presigned_url: str, payload: bytes, content_type: str):
    async with session.put(
        presigned_url,
        data=payload,
        headers={"Content-Type": content_type},
        timeout=UPLOAD_TIMEOUT,
    ) as resp:
        if resp.status >= 400:
            body = (await resp.text())[:500]
            raise RuntimeError(f"presigned PUT failed status={resp.status} body={body!r}")


async def _poll_image_status(
    session: aiohttp.ClientSession,
    file_ticket: str,
    *,
    attempts: int = 10,
    interval: float = 3,
) -> dict:
    """Polling (default 3s × 10 = до 30s). status==1 → success, status==2 → failed.

    Видео обрабатывается тем же endpoint'ом, но дольше — вызывать с бОльшим attempts.
    """
    for attempt in range(attempts):
        data = await _post_json(
            session,
            f"{BINANCE_API_V2}/image/imageStatus",
            {"fileTicket": file_ticket},
            timeout=POLL_TIMEOUT,
        )
        payload = data.get("data") or {}
        status = payload.get("status")
        if status == 1:
            return payload
        if status == 2:
            reason = payload.get("failedReason") or describe_error_code(data.get("code"), data.get("message"))
            raise UploadError(f"imageStatus failed: {reason}", kind=classify_code(data.get("code")), code=data.get("code"))
        logger.info(
            "binance imageStatus: ticket=%s attempt=%d status=%s code=%s",
            file_ticket, attempt + 1, status, data.get("code"),
        )
        await asyncio.sleep(interval)
    raise RuntimeError(f"imageStatus timeout after {attempts} attempts (ticket={file_ticket})")


async def upload_image(session: aiohttp.ClientSession, image_bytes: bytes, image_name: str = "photo.jpg") -> str:
    """Полный image upload flow → возвращает processed imageUrl."""
    presigned = await _request_image_presigned(session, image_name)
    if presigned.get("code") != "000000":
        code = presigned.get("code")
        raise UploadError(
            f"presignedUrl failed: {describe_error_code(code, presigned.get('message'))}",
            kind=classify_code(code),
            code=code,
        )
    pdata = presigned.get("data") or {}
    presigned_url = pdata.get("presignedUrl")
    file_ticket = pdata.get("fileTicket")
    if not presigned_url or not file_ticket:
        raise RuntimeError(f"presignedUrl: missing presignedUrl/fileTicket in {pdata!r}")

    await _put_bytes(session, presigned_url, image_bytes, _content_type_for(image_name))

    status_payload = await _poll_image_status(session, file_ticket)
    image_url = status_payload.get("imageUrl")
    if not image_url:
        raise RuntimeError(f"imageStatus: no imageUrl in payload {status_payload!r}")
    return image_url


VIDEO_UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=600)


async def upload_video(session: aiohttp.ClientSession, video_bytes: bytes, video_name: str = "video.mp4") -> str:
    """Video upload flow → возвращает fileTicket (его требует content/add contentType=3,
    в отличие от картинок, где в imageList кладутся processed imageUrl).

    Presign у видео СВОЙ: POST /video/preSign {fileName, size} (официальный
    post-video.mjs из binance-skills-hub) — не image/presignedUrl. Polling статуса —
    тот же /image/imageStatus, но обработка видео дольше → 5s × 36 = до 3 мин.
    """
    presigned = await _post_json(
        session,
        f"{BINANCE_API_V2}/video/preSign",
        {"fileName": video_name, "size": len(video_bytes)},
    )
    if presigned.get("code") != "000000":
        code = presigned.get("code")
        raise UploadError(
            f"video preSign failed: {describe_error_code(code, presigned.get('message'))}",
            kind=classify_code(code),
            code=code,
        )
    pdata = presigned.get("data") or {}
    presigned_url = pdata.get("presignedUrl")
    file_ticket = pdata.get("fileTicket")
    if not presigned_url or not file_ticket:
        raise RuntimeError(f"video preSign: missing presignedUrl/fileTicket in {pdata!r}")

    async with session.put(
        presigned_url,
        data=video_bytes,
        headers={"Content-Type": _content_type_for(video_name)},
        timeout=VIDEO_UPLOAD_TIMEOUT,
    ) as resp:
        if resp.status >= 400:
            body = (await resp.text())[:500]
            raise RuntimeError(f"video presigned PUT failed status={resp.status} body={body!r}")

    await _poll_image_status(session, file_ticket, attempts=36, interval=5)
    return file_ticket


def _interpret_publish_response(data: dict) -> BinanceResult:
    code = data.get("code")
    payload = data.get("data") or {}
    if code == "000000":
        post_id = payload.get("id") or payload.get("postId")
        share_link = payload.get("shareLink")
        url = share_link or (f"https://www.binance.com/square/post/{post_id}" if post_id else None)
        return BinanceResult(ok=True, post_id=str(post_id) if post_id else None, url=url, raw=data)
    # 504 → still considered success per official skill helper
    if data.get("_http_status") == 504:
        logger.warning("binance content/add returned 504 — treating as success without post id")
        return BinanceResult(ok=True, raw=data)
    http = data.get("_http_status")
    kind = classify_code(code) if code else ("transient" if (http and http >= 500) else "transient")
    return BinanceResult(
        ok=False,
        error=describe_error_code(code, data.get("message")),
        kind=kind,
        raw=data,
    )


async def _publish(body: dict, *, images_attached: int = 0) -> BinanceResult:
    if not BINANCE_API_KEY:
        return BinanceResult(ok=False, error="BINANCE_SQUARE_API_KEY not set", kind="auth")
    try:
        async with aiohttp.ClientSession() as session:
            data = await _post_json(session, f"{BINANCE_API_V1}/content/add", body)
            result = _interpret_publish_response(data)
            if result.ok:
                result.images_attached = images_attached
            return result
    except asyncio.TimeoutError as e:
        return BinanceResult(ok=False, error=f"timeout: {e}", kind="transient")
    except Exception as e:
        logger.error("binance publish error: %s", e)
        return BinanceResult(ok=False, error=str(e), kind="transient")


async def publish_text(text: str) -> BinanceResult:
    """contentType=1, text only."""
    return await _publish({"contentType": 1, "bodyTextOnly": text})


async def publish_image_post(
    text: str,
    image_bytes_list: list[tuple[bytes, str]],
) -> BinanceResult:
    """Short image post: contentType=1 + bodyTextOnly + imageList[].

    image_bytes_list: до 4 (bytes, filename) — порядок сохраняется.

    Если ВСЕ картинки не загрузились — НЕ публикуем молча голым текстом
    (иначе картинка теряется навсегда). Возвращаем неуспех с kind, чтобы
    планировщик подержал/переретраил пост С картинкой. Старое поведение
    (fallback на текст) — за флагом BINANCE_IMAGE_FALLBACK_TEXT.
    """
    if not BINANCE_API_KEY:
        return BinanceResult(ok=False, error="BINANCE_SQUARE_API_KEY not set", kind="auth")
    wanted = image_bytes_list[:4]
    image_urls: list[str] = []
    fail_kind: str | None = None
    fail_msgs: list[str] = []
    try:
        async with aiohttp.ClientSession() as session:
            for idx, (bts, name) in enumerate(wanted):
                try:
                    url = await upload_image(session, bts, name)
                    image_urls.append(url)
                    logger.info("binance image %d/%d uploaded: %s", idx + 1, len(wanted), url)
                except UploadError as e:
                    fail_kind = _worse_kind(fail_kind, e.kind)
                    fail_msgs.append(str(e))
                    logger.error("binance image %d upload failed (%s): %s", idx + 1, e.kind, e)
                except Exception as e:
                    fail_kind = _worse_kind(fail_kind, "transient")
                    fail_msgs.append(str(e))
                    logger.error("binance image %d upload failed: %s", idx + 1, e)
    except Exception as e:
        fail_kind = _worse_kind(fail_kind, "transient")
        fail_msgs.append(str(e))
        logger.error("binance upload session error: %s", e)

    if not image_urls:
        if BINANCE_IMAGE_FALLBACK_TEXT:
            logger.warning("binance: no images uploaded, FALLBACK to text-only (flag on)")
            return await publish_text(text)
        logger.warning("binance: no images uploaded — НЕ публикуем text-only, оставляем на ретрай (kind=%s)", fail_kind)
        return BinanceResult(
            ok=False,
            kind=fail_kind or "transient",
            error="image upload failed: " + " | ".join(fail_msgs[:2]),
        )

    body: dict = {"contentType": 1, "bodyTextOnly": text or "", "imageList": image_urls}
    return await _publish(body, images_attached=len(image_urls))


async def publish_video_post(
    text: str,
    video: tuple[bytes, str],
    duration_seconds: float,
    cover: tuple[bytes, str] | None = None,
) -> BinanceResult:
    """contentType=3 video post: fileTicket + cover + videoTimeSeconds + isPublish.

    cover — превью-кадр (у Telegram-видео это thumbnail); грузится как обычная
    картинка → imageUrl. Если cover не загрузился — публикуем без него (видео
    важнее превью, Binance сгенерит своё).

    Если видео не загрузилось — как и с картинками, НЕ публикуем text-only
    (видео потеряется навсегда), возвращаем неуспех с kind для retry-политики.
    Fallback на текст — за тем же флагом BINANCE_IMAGE_FALLBACK_TEXT.
    """
    if not BINANCE_API_KEY:
        return BinanceResult(ok=False, error="BINANCE_SQUARE_API_KEY not set", kind="auth")
    try:
        async with aiohttp.ClientSession() as session:
            try:
                file_ticket = await upload_video(session, video[0], video[1])
                logger.info("binance video uploaded: ticket=%s (%d bytes)", file_ticket, len(video[0]))
            except UploadError as e:
                logger.error("binance video upload failed (%s): %s", e.kind, e)
                if BINANCE_IMAGE_FALLBACK_TEXT and text:
                    logger.warning("binance: video upload failed, FALLBACK to text-only (flag on)")
                    return await publish_text(text)
                return BinanceResult(ok=False, kind=e.kind, error=f"video upload failed: {e}")

            cover_url: str | None = None
            if cover is not None:
                try:
                    cover_url = await upload_image(session, cover[0], cover[1])
                except Exception as e:
                    logger.error("binance video cover upload failed, publishing without cover: %s", e)
    except Exception as e:
        logger.error("binance video upload error: %s", e)
        if BINANCE_IMAGE_FALLBACK_TEXT and text:
            return await publish_text(text)
        return BinanceResult(ok=False, kind="transient", error=f"video upload failed: {e}")

    body: dict = {
        "contentType": 3,
        "fileTicket": file_ticket,
        "videoTimeSeconds": float(duration_seconds or 0),
        "isPublish": True,
    }
    # официальный post-video.mjs добавляет bodyTextOnly ТОЛЬКО при непустом тексте
    if text:
        body["bodyTextOnly"] = text
    if cover_url:
        body["cover"] = cover_url
    result = await _publish(body)
    if result.ok:
        result.video_attached = True
    return result


async def publish_article(
    title: str,
    body_text: str,
    cover_bytes: tuple[bytes, str] | None = None,
) -> BinanceResult:
    """contentType=2 article. cover необязателен.

    Caveat (verified 2026-05-28): Binance Square frontend renders the `cover`
    inconsistently on the article *detail* page when the article is published
    through this public OpenAPI path. Same `cover` asset typically does appear
    on profile/feed cards, but the article detail view often shows no hero
    image for API-published articles, while native-Editor articles do render
    one. The API call itself is correct and supported; the discrepancy lives
    on the rendering side. Prefer `publish_image_post` (contentType=1 with
    imageList) when you need a guaranteed visible image.
    """
    if not BINANCE_API_KEY:
        return BinanceResult(ok=False, error="BINANCE_SQUARE_API_KEY not set")
    body: dict = {"contentType": 2, "title": title, "bodyTextOnly": body_text}
    if cover_bytes is not None:
        try:
            async with aiohttp.ClientSession() as session:
                body["cover"] = await upload_image(session, cover_bytes[0], cover_bytes[1])
        except Exception as e:
            logger.error("binance cover upload failed, posting article without cover: %s", e)
    return await _publish(body)


async def publish_post(text: str, image_bytes_list: list[tuple[bytes, str]] | None = None) -> BinanceResult:
    """High-level dispatcher: с картинками или без."""
    if image_bytes_list:
        return await publish_image_post(text, image_bytes_list)
    return await publish_text(text)
