from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

from config import IMGBB_API_KEY, logger


def _log_upload_failure(service: str, status: int | None, body: str | None, error: Exception | None = None):
    if error is not None:
        logger.error("upload_image [%s] exception: %s", service, error)
    else:
        logger.error("upload_image [%s] failed: status=%s body=%.200r", service, status, body)
    sys.stderr.flush()


async def upload_image(image_bytes: bytes) -> str | None:
    """Upload one image and return a public URL. Tries imgbb (if key) → catbox → 0x0.st."""
    logger.info("upload_image: starting upload, size=%d bytes", len(image_bytes))
    sys.stderr.flush()

    if IMGBB_API_KEY:
        try:
            async with aiohttp.ClientSession() as s:
                form = aiohttp.FormData()
                form.add_field("key", IMGBB_API_KEY)
                form.add_field("image", image_bytes, filename="photo.jpg", content_type="image/jpeg")
                async with s.post(
                    "https://api.imgbb.com/1/upload",
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    body = await resp.text()
                    logger.info("upload_image [imgbb]: status=%d body=%.200r", resp.status, body)
                    if resp.status == 200:
                        try:
                            payload = json.loads(body)
                        except json.JSONDecodeError:
                            payload = {}
                        img_url = payload.get("data", {}).get("url")
                        if img_url:
                            logger.info("upload_image [imgbb]: success url=%s", img_url)
                            return img_url
                    _log_upload_failure("imgbb", resp.status, body)
        except asyncio.TimeoutError as e:
            logger.error("upload_image [imgbb]: timeout after 30s: %s", e)
        except Exception as e:
            _log_upload_failure("imgbb", None, None, error=e)
    else:
        logger.info("upload_image: IMGBB_API_KEY not set, skipping imgbb")

    fallbacks = [
        ("catbox", "https://catbox.moe/user/api.php", {"reqtype": "fileupload"}, "fileToUpload"),
        ("0x0.st", "https://0x0.st", {}, "file"),
    ]
    for name, url, extra_fields, file_field in fallbacks:
        try:
            async with aiohttp.ClientSession() as s:
                form = aiohttp.FormData()
                for k, v in extra_fields.items():
                    form.add_field(k, v)
                form.add_field(file_field, image_bytes, filename="photo.jpg", content_type="image/jpeg")
                async with s.post(url, data=form, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    body = (await resp.text()).strip()
                    logger.info("upload_image [%s]: status=%d body=%.200r", name, resp.status, body)
                    if resp.status == 200 and body.startswith("https://"):
                        return body
                    _log_upload_failure(name, resp.status, body)
        except asyncio.TimeoutError as e:
            logger.error("upload_image [%s]: timeout after 30s: %s", name, e)
        except Exception as e:
            _log_upload_failure(name, None, None, error=e)

    logger.error("upload_image: all services failed for %d-byte image", len(image_bytes))
    return None
