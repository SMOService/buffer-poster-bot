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

# Известные коды ошибок (skill docs / Academy 2026-03).
ERROR_CODES = {
    "220003": "API key not found",
    "220004": "API key expired",
    "220009": "daily post limit exceeded (100/day)",
    "220014": "daily upload limit exceeded (400/day)",
    "20002": "sensitive words detected",
    "20013": "content length limit exceeded",
    "20022": "sensitive words detected",
}


@dataclass
class BinanceResult:
    ok: bool
    post_id: str | None = None
    url: str | None = None
    error: str | None = None
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


async def _poll_image_status(session: aiohttp.ClientSession, file_ticket: str) -> dict:
    """Polling: 3s × 10 = до 30s. status==1 → success, status==2 → failed."""
    for attempt in range(10):
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
            raise RuntimeError(f"imageStatus failed: {reason}")
        logger.info(
            "binance imageStatus: ticket=%s attempt=%d status=%s code=%s",
            file_ticket, attempt + 1, status, data.get("code"),
        )
        await asyncio.sleep(3)
    raise RuntimeError(f"imageStatus timeout after 10 attempts (ticket={file_ticket})")


async def upload_image(session: aiohttp.ClientSession, image_bytes: bytes, image_name: str = "photo.jpg") -> str:
    """Полный image upload flow → возвращает processed imageUrl."""
    presigned = await _request_image_presigned(session, image_name)
    if presigned.get("code") != "000000":
        raise RuntimeError(
            f"presignedUrl failed: {describe_error_code(presigned.get('code'), presigned.get('message'))}"
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
    return BinanceResult(
        ok=False,
        error=describe_error_code(code, data.get("message")),
        raw=data,
    )


async def _publish(body: dict) -> BinanceResult:
    if not BINANCE_API_KEY:
        return BinanceResult(ok=False, error="BINANCE_SQUARE_API_KEY not set")
    try:
        async with aiohttp.ClientSession() as session:
            data = await _post_json(session, f"{BINANCE_API_V1}/content/add", body)
            return _interpret_publish_response(data)
    except asyncio.TimeoutError as e:
        return BinanceResult(ok=False, error=f"timeout: {e}")
    except Exception as e:
        logger.error("binance publish error: %s", e)
        return BinanceResult(ok=False, error=str(e))


async def publish_text(text: str) -> BinanceResult:
    """contentType=1, text only."""
    return await _publish({"contentType": 1, "bodyTextOnly": text})


async def publish_image_post(
    text: str,
    image_bytes_list: list[tuple[bytes, str]],
) -> BinanceResult:
    """Short image post: contentType=1 + bodyTextOnly + imageList[].

    image_bytes_list: до 4 (bytes, filename) — порядок сохраняется.
    Если все картинки фейлятся, fallback на publish_text.
    """
    if not BINANCE_API_KEY:
        return BinanceResult(ok=False, error="BINANCE_SQUARE_API_KEY not set")
    image_urls: list[str] = []
    try:
        async with aiohttp.ClientSession() as session:
            for idx, (bts, name) in enumerate(image_bytes_list[:4]):
                try:
                    url = await upload_image(session, bts, name)
                    image_urls.append(url)
                    logger.info("binance image %d/%d uploaded: %s", idx + 1, len(image_bytes_list[:4]), url)
                except Exception as e:
                    logger.error("binance image %d upload failed: %s", idx + 1, e)
    except Exception as e:
        logger.error("binance upload session error: %s", e)

    if not image_urls:
        logger.warning("binance: no images uploaded, falling back to text-only post")
        return await publish_text(text)

    body: dict = {"contentType": 1, "bodyTextOnly": text or "", "imageList": image_urls}
    return await _publish(body)


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
