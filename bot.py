import asyncio
import hashlib
import logging
import os
import random
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
    force=True,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
BUFFER_TOKEN = os.environ["BUFFER_ACCESS_TOKEN"]
BINANCE_API_KEY = os.environ.get("BINANCE_SQUARE_API_KEY", "")
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "")

SCHEDULE_MIN_HOURS = float(os.environ.get("SCHEDULE_MIN_HOURS", "1"))
SCHEDULE_MAX_HOURS = float(os.environ.get("SCHEDULE_MAX_HOURS", "240"))

BUFFER_API = "https://api.buffer.com"
BINANCE_SQUARE_API = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"

DB_PATH = Path(os.environ.get("DB_PATH", "/app/data/bot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

SERVICE_EMOJI = {
    "twitter": "🐦", "linkedin": "💼", "threads": "🧵",
    "instagram": "📸", "facebook": "👤", "tiktok": "🎵",
    "mastodon": "🐘", "bluesky": "🦋", "pinterest": "📌",
}

# Буфер для группировки альбомов: {media_group_id: {"photos": [...], "text": str, "task": Task}}
album_buffer: dict = {}


# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY, name TEXT, service TEXT, enabled INTEGER DEFAULT 1
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS binance_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            image_url TEXT,
            publish_at INTEGER NOT NULL,
            published INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS published_hashes (
            hash TEXT PRIMARY KEY,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        )""")
        conn.commit()

def text_hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()

def is_duplicate(text: str) -> bool:
    if not text:
        return False
    h = text_hash(text)
    with get_conn() as conn:
        row = conn.execute("SELECT hash FROM published_hashes WHERE hash=?", (h,)).fetchone()
        return row is not None

def save_hash(text: str):
    if not text:
        return
    h = text_hash(text)
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO published_hashes (hash) VALUES (?)", (h,))
        conn.commit()

def save_channels(channels: list[dict]):
    with get_conn() as conn:
        existing = {r["id"] for r in conn.execute("SELECT id FROM channels").fetchall()}
        for ch in channels:
            if ch["id"] not in existing:
                conn.execute(
                    "INSERT INTO channels (id, name, service, enabled) VALUES (?,?,?,1)",
                    (ch["id"], ch["name"], ch["service"])
                )
            else:
                conn.execute(
                    "UPDATE channels SET name=?, service=? WHERE id=?",
                    (ch["name"], ch["service"], ch["id"])
                )
        conn.commit()

def get_all_channels_db() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM channels ORDER BY service").fetchall()]

def get_enabled_channels_db() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM channels WHERE enabled=1").fetchall()]

def toggle_channel(channel_id: str, enabled: bool):
    with get_conn() as conn:
        conn.execute("UPDATE channels SET enabled=? WHERE id=?", (1 if enabled else 0, channel_id))
        conn.commit()

def build_channels_msg(all_ch: list[dict]) -> dict:
    buttons = []
    for ch in all_ch:
        tick = "✅" if ch["enabled"] else "☑️"
        label = f"{tick} {SERVICE_EMOJI.get(ch['service'], '•')} {ch['name']} ({ch['service']})"
        action = "off" if ch["enabled"] else "on"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"ch_{action}_{ch['id']}")])
    buttons.append([InlineKeyboardButton(text="🔄 Обновить из Buffer", callback_data="ch_refresh")])
    enabled = sum(1 for c in all_ch if c["enabled"])
    return {
        "text": f"<b>Каналы ({enabled}/{len(all_ch)} активных):</b>\n\nНажми для включения/выключения:",
        "reply_markup": InlineKeyboardMarkup(inline_keyboard=buttons),
        "parse_mode": "HTML"
    }

def add_to_binance_queue(text: str, image_url: str | None, due_at_iso: str):
    """Add a post to the Binance queue using the same ISO datetime as Buffer."""
    dt = datetime.fromisoformat(due_at_iso.replace("Z", "+00:00"))
    publish_at = int(dt.timestamp())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO binance_queue (text, image_url, publish_at) VALUES (?, ?, ?)",
            (text, image_url, publish_at)
        )
        conn.commit()

def get_pending_binance_posts() -> list[dict]:
    now = int(datetime.now(timezone.utc).timestamp())
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM binance_queue WHERE published=0 AND publish_at<=? ORDER BY publish_at",
            (now,)
        ).fetchall()]

def mark_binance_published(post_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE binance_queue SET published=1 WHERE id=?", (post_id,))
        conn.commit()

def get_binance_queue_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM binance_queue WHERE published=0").fetchone()["c"]
        next_row = conn.execute(
            "SELECT publish_at FROM binance_queue WHERE published=0 ORDER BY publish_at LIMIT 1"
        ).fetchone()
    return {"total": total, "next_at": next_row["publish_at"] if next_row else None}


# ── Buffer API ────────────────────────────────────────────────────────────────

async def buffer_query(query: str, variables: dict = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    async with aiohttp.ClientSession() as s:
        async with s.post(
            BUFFER_API, json=payload,
            headers={"Authorization": f"Bearer {BUFFER_TOKEN}", "Content-Type": "application/json"}
        ) as resp:
            return await resp.json()

async def fetch_channels_from_buffer() -> list[dict]:
    data = await buffer_query("query { account { organizations { channels { id name service } } } }")
    channels = []
    try:
        for org in data["data"]["account"]["organizations"]:
            for ch in org.get("channels", []):
                channels.append({
                    "id": ch["id"],
                    "name": ch.get("name") or ch["service"],
                    "service": ch["service"]
                })
    except (KeyError, TypeError) as e:
        logger.error("fetch_channels error: %s | %s", e, data)
    return channels

def _log_upload_failure(service: str, status: int | None, body: str | None, error: Exception | None = None):
    """Structured logging for image upload failures."""
    if error is not None:
        logger.error("upload_image [%s] exception: %s", service, error)
    else:
        logger.error(
            "upload_image [%s] failed: status=%s body=%.200r",
            service, status, body
        )
    sys.stderr.flush()

async def upload_image(image_bytes: bytes) -> str | None:
    logger.info("upload_image: starting upload, size=%d bytes", len(image_bytes))
    sys.stderr.flush()

    # ── imgbb (primary) ───────────────────────────────────────────────────────
    if IMGBB_API_KEY:
        logger.info("upload_image: trying service=imgbb url=https://api.imgbb.com/1/upload")
        sys.stderr.flush()
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
                    sys.stderr.flush()
                    if resp.status == 200:
                        import json as _json
                        payload = _json.loads(body)
                        img_url = payload.get("data", {}).get("url")
                        if img_url:
                            logger.info("upload_image [imgbb]: success url=%s", img_url)
                            sys.stderr.flush()
                            return img_url
                        else:
                            _log_upload_failure("imgbb", resp.status, body)
                    else:
                        _log_upload_failure("imgbb", resp.status, body)
        except asyncio.TimeoutError as e:
            logger.error("upload_image [imgbb]: timeout after 30s: %s", e)
            sys.stderr.flush()
        except Exception as e:
            _log_upload_failure("imgbb", None, None, error=e)
    else:
        logger.info("upload_image: IMGBB_API_KEY not set, skipping imgbb")
        sys.stderr.flush()

    # ── fallback services ─────────────────────────────────────────────────────
    for attempt in [
        ("catbox", "https://catbox.moe/user/api.php", {"reqtype": "fileupload"}, "fileToUpload"),
        ("0x0.st", "https://0x0.st", {}, "file"),
    ]:
        name, url, extra_fields, file_field = attempt
        logger.info("upload_image: trying service=%s url=%s", name, url)
        sys.stderr.flush()
        try:
            async with aiohttp.ClientSession() as s:
                form = aiohttp.FormData()
                for k, v in extra_fields.items():
                    form.add_field(k, v)
                form.add_field(file_field, image_bytes, filename="photo.jpg", content_type="image/jpeg")
                async with s.post(url, data=form, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    body = (await resp.text()).strip()
                    logger.info(
                        "upload_image [%s]: status=%d body=%.200r",
                        name, resp.status, body
                    )
                    sys.stderr.flush()
                    if resp.status == 200 and body.startswith("https://"):
                        logger.info("upload_image [%s]: success url=%s", name, body)
                        sys.stderr.flush()
                        return body
                    else:
                        _log_upload_failure(name, resp.status, body)
        except asyncio.TimeoutError as e:
            logger.error("upload_image [%s]: timeout after 30s: %s", name, e)
            sys.stderr.flush()
        except Exception as e:
            _log_upload_failure(name, None, None, error=e)

    logger.error("upload_image: all services failed for %d-byte image", len(image_bytes))
    sys.stderr.flush()
    return None

async def buffer_create_post(channel_id: str, text: str, image_urls: list[str], due_at: str, service: str = "") -> dict:
    """Создаёт пост в Buffer. Поддерживает до 4 изображений (карусель для Twitter).

    Schema drift 2026: `assets` в CreatePostInput стал обязательным списком
    [AssetInput!]! — для текстового поста передаём пустой список, для постов
    с картинками — по {image:{url}} на каждое изображение (ImageAssetInput.url).
    """
    assets = [{"image": {"url": u}} for u in image_urls[:4]]
    mutation = """
    mutation CreatePost($cid:ChannelId!,$text:String!,$due:DateTime,$assets:[AssetInput!]!){
      createPost(input:{channelId:$cid,text:$text,schedulingType:automatic,
        mode:customScheduled,dueAt:$due,assets:$assets}){
        ...on PostActionSuccess{post{id}}
        ...on MutationError{message}
      }
    }"""
    variables = {"cid": channel_id, "text": text, "due": due_at, "assets": assets}
    return await buffer_query(mutation, variables)


# ── Binance Square ────────────────────────────────────────────────────────────

async def binance_publish_post(text: str) -> dict | None:
    if not BINANCE_API_KEY:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                BINANCE_SQUARE_API,
                json={"bodyTextOnly": text},
                headers={
                    "X-Square-OpenAPI-Key": BINANCE_API_KEY,
                    "Content-Type": "application/json",
                    "clienttype": "web"
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                return await resp.json()
    except Exception as e:
        logger.error("Binance Square error: %s", e)
        return None

async def binance_scheduler():
    while True:
        try:
            pending = get_pending_binance_posts()
            logger.info("Binance scheduler: checking pending posts (found %d due)", len(pending))
            for post in pending:
                logger.info(
                    "Binance scheduler: publishing post id=%d, text=%.60r, publish_at=%d",
                    post["id"], post["text"], post["publish_at"]
                )
                result = await binance_publish_post(post["text"])
                if result and result.get("code") == "000000":
                    mark_binance_published(post["id"])
                    post_id = result.get("data", {}).get("id", "")
                    url = f"https://www.binance.com/square/post/{post_id}" if post_id else ""
                    logger.info("Binance scheduler: published post id=%d, url=%s", post["id"], url or "(no url)")
                    try:
                        await bot.send_message(
                            ALLOWED_USER_ID,
                            f"✅ <b>Binance Square опубликовано</b>\n\n"
                            f"<i>{post['text'][:80]}{'…' if len(post['text']) > 80 else ''}</i>"
                            f"{chr(10) + url if url else ''}",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                else:
                    logger.error(
                        "Binance scheduler: failed to publish post id=%d, response=%s",
                        post["id"], result
                    )
            if not pending:
                logger.info("Binance scheduler: no posts due, sleeping")
        except Exception as e:
            logger.error("Binance scheduler error: %s", e)
        await asyncio.sleep(60)


# ── Helpers ───────────────────────────────────────────────────────────────────

def random_due_at() -> str:
    offset = random.randint(int(SCHEDULE_MIN_HOURS * 3600), int(SCHEDULE_MAX_HOURS * 3600))
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

def fmt_due(due_at: str) -> str:
    dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    return dt.strftime("%d %b %Y %H:%M UTC")

def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")

def is_me(message: Message) -> bool:
    return message.from_user.id == ALLOWED_USER_ID

async def process_post(message: Message, text: str, photo_bytes_list: list[bytes]):
    """Основная логика публикации — вызывается и для одиночных постов и для альбомов."""
    # Проверка дублей
    if text and is_duplicate(text):
        await message.answer(
            f"⚠️ <b>Дубликат!</b> Этот пост уже публиковался ранее.\n\n"
            f"<i>{text[:80]}{'…' if len(text) > 80 else ''}</i>",
            parse_mode="HTML"
        )
        return

    # Загружаем все фото
    logger.info("process_post: uploading %d photo(s)", len(photo_bytes_list))
    sys.stderr.flush()
    image_urls = []
    for i, photo_bytes in enumerate(photo_bytes_list):
        logger.info("process_post: uploading photo %d/%d (%d bytes)", i + 1, len(photo_bytes_list), len(photo_bytes))
        sys.stderr.flush()
        url = await upload_image(photo_bytes)
        if url:
            logger.info("process_post: photo %d/%d uploaded: %s", i + 1, len(photo_bytes_list), url)
            sys.stderr.flush()
            image_urls.append(url)
        else:
            logger.error("process_post: photo %d/%d upload failed", i + 1, len(photo_bytes_list))
            sys.stderr.flush()

    if photo_bytes_list and not image_urls:
        logger.error("process_post: all photos failed to upload, aborting")
        sys.stderr.flush()
        await message.answer("❌ Не удалось загрузить фото.")
        return

    if not text and not image_urls:
        return

    result_lines = []

    # Compute a single due_at used for both Buffer and Binance to keep them in sync
    due_at = random_due_at()

    # ── Buffer ──
    channels = get_enabled_channels_db()
    if channels:
        success, failed = [], []
        for ch in channels:
            result = await buffer_create_post(ch["id"], text, image_urls, due_at, ch.get("service", ""))
            try:
                post_result = result["data"]["createPost"]
                if "post" in post_result:
                    success.append(ch)
                else:
                    logger.error("Buffer error %s: %s", ch["id"], post_result.get("message"))
                    failed.append(ch)
            except (KeyError, TypeError) as e:
                logger.error("Buffer unexpected %s: %s | %s", ch["id"], e, result)
                failed.append(ch)

        result_lines.append(f"<b>Buffer</b> ⏰ {fmt_due(due_at)}")
        for ch in success:
            result_lines.append(f"  ✅ {SERVICE_EMOJI.get(ch['service'], '•')} {ch['name']}")
        for ch in failed:
            result_lines.append(f"  ❌ {SERVICE_EMOJI.get(ch['service'], '•')} {ch['name']}")

    # ── Binance Square ──
    if BINANCE_API_KEY and text:
        add_to_binance_queue(text, image_urls[0] if image_urls else None, due_at)
        result_lines.append(f"\n<b>Binance Square</b> ⏰ {fmt_due(due_at)}")
        result_lines.append("  📥 добавлен в очередь")
    elif BINANCE_API_KEY and not text:
        result_lines.append("\n<b>Binance Square</b>: пропущен (нет текста)")

    if image_urls:
        result_lines.append(f"\n🖼 фото: {len(image_urls)} шт.")
    if text:
        result_lines.append(f"<i>{text[:80]}{'…' if len(text) > 80 else ''}</i>")

    # Сохраняем хэш
    save_hash(text)

    await message.answer("\n".join(result_lines), parse_mode="HTML")


# ── Handlers ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not is_me(message): return
    channels = get_enabled_channels_db()
    ch_lines = "\n".join(
        f"  {SERVICE_EMOJI.get(c['service'], '•')} {c['name']} ({c['service']})"
        for c in channels
    ) or "  нет активных каналов"

    binance_status = "✅ подключён" if BINANCE_API_KEY else "❌ не настроен"
    stats = get_binance_queue_stats()
    next_binance = f"\n  следующий: {fmt_ts(stats['next_at'])}" if stats["next_at"] else ""

    await message.answer(
        f"👋 <b>Buffer Poster Bot</b>\n\n"
        f"<b>Активных каналов Buffer ({len(channels)}):</b>\n{ch_lines}\n\n"
        f"<b>Binance Square:</b> {binance_status}\n"
        f"  в очереди: {stats['total']} постов{next_binance}\n\n"
        f"<b>Расписание:</b> случайно {int(SCHEDULE_MIN_HOURS)}–{int(SCHEDULE_MAX_HOURS)} ч\n\n"
        f"Пересылай посты — уйдут в Buffer и Binance Square автоматически.\n\n"
        f"/channels — управление каналами\n"
        f"/queue — очереди\n"
        f"/binance — очередь Binance Square",
        parse_mode="HTML"
    )


@dp.message(Command("channels"))
async def cmd_channels(message: Message):
    if not is_me(message): return
    all_ch = get_all_channels_db()
    if not all_ch:
        return await message.answer("Каналы не найдены. Перезапусти бота.")
    await message.answer(**build_channels_msg(all_ch))


@dp.callback_query(F.data.startswith("ch_"))
async def cb_channel(call: CallbackQuery):
    if call.from_user.id != ALLOWED_USER_ID: return
    parts = call.data.split("_", 2)
    action = parts[1]

    if action == "refresh":
        channels = await fetch_channels_from_buffer()
        if channels:
            save_channels(channels)
            await call.answer(f"Обновлено: {len(channels)} каналов", show_alert=True)
        else:
            await call.answer("Ошибка при обновлении", show_alert=True)
        await call.message.edit_text(**build_channels_msg(get_all_channels_db()))
        return

    toggle_channel(parts[2], action == "on")
    await call.answer("Сохранено")
    await call.message.edit_text(**build_channels_msg(get_all_channels_db()))


@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    if not is_me(message): return
    channels = get_enabled_channels_db()
    buffer_lines = []
    if channels:
        query = """
        query($cid:ChannelId!){
          channel(id:$cid){
            posts(filter:{status:scheduled},first:100){ edges{ node{ id dueAt } } }
          }
        }"""
        for ch in channels:
            data = await buffer_query(query, {"cid": ch["id"]})
            try:
                count = len(data["data"]["channel"]["posts"]["edges"])
            except (KeyError, TypeError):
                count = "?"
            buffer_lines.append(f"{SERVICE_EMOJI.get(ch['service'], '•')} <b>{ch['name']}</b>: {count} постов")

    stats = get_binance_queue_stats()
    next_binance = f"\n  следующий: {fmt_ts(stats['next_at'])}" if stats["next_at"] else ""
    text = "<b>Очередь Buffer:</b>\n" + "\n".join(buffer_lines) if buffer_lines else "<b>Buffer:</b> нет активных каналов"
    text += f"\n\n<b>Binance Square:</b> {stats['total']} постов{next_binance}"
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("binance"))
async def cmd_binance(message: Message):
    if not is_me(message): return
    now = int(datetime.now(timezone.utc).timestamp())
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, text, publish_at FROM binance_queue WHERE published=0 ORDER BY publish_at LIMIT 10"
        ).fetchall()
    if not rows:
        return await message.answer("📭 Очередь Binance Square пуста.")
    lines = [f"<b>Binance Square — следующие {len(rows)} постов:</b>\n"]
    buttons = []
    for r in rows:
        overdue = r["publish_at"] < now
        icon = "🔴" if overdue else "⏰"
        time_label = fmt_ts(r["publish_at"])
        if overdue:
            delta = now - r["publish_at"]
            h, m = divmod(delta // 60, 60)
            time_label += f" (просрочен на {h}ч {m}м)" if h else f" (просрочен на {m}м)"
        else:
            delta = r["publish_at"] - now
            h, m = divmod(delta // 60, 60)
            time_label += f" (через {h}ч {m}м)" if h else f" (через {m}м)"
        preview = r["text"][:60] + ("…" if len(r["text"]) > 60 else "")
        lines.append(f"{icon} {time_label}\n<i>{preview}</i>\n")
        buttons.append([InlineKeyboardButton(
            text=f"📤 Send Now (id={r['id']})",
            callback_data=f"binance_send_{r['id']}"
        )])
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.callback_query(F.data.startswith("binance_send_"))
async def cb_binance_send(call: CallbackQuery):
    if call.from_user.id != ALLOWED_USER_ID:
        return
    post_id = int(call.data.removeprefix("binance_send_"))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, text, published FROM binance_queue WHERE id=?", (post_id,)
        ).fetchone()
    if not row:
        await call.answer("❌ Пост не найден", show_alert=True)
        return
    if row["published"]:
        await call.answer("ℹ️ Пост уже опубликован", show_alert=True)
        return
    await call.answer("⏳ Публикую…")
    logger.info("Manual Binance publish triggered for post id=%d by user %d", post_id, call.from_user.id)
    result = await binance_publish_post(row["text"])
    if result and result.get("code") == "000000":
        mark_binance_published(post_id)
        binance_post_id = result.get("data", {}).get("id", "")
        url = f"https://www.binance.com/square/post/{binance_post_id}" if binance_post_id else ""
        logger.info("Manual Binance publish succeeded for post id=%d, url=%s", post_id, url or "(no url)")
        preview = row["text"][:80] + ("…" if len(row["text"]) > 80 else "")
        await call.message.edit_text(
            f"✅ <b>Binance Square опубликовано вручную</b>\n\n"
            f"<i>{preview}</i>"
            f"{chr(10) + url if url else ''}",
            parse_mode="HTML"
        )
    else:
        logger.error("Manual Binance publish failed for post id=%d, response=%s", post_id, result)
        await call.message.edit_text(
            f"❌ <b>Ошибка публикации Binance Square</b>\n\n"
            f"post id={post_id}\n"
            f"response: <code>{result}</code>",
            parse_mode="HTML"
        )


# ── Обработка альбомов (карусель) ─────────────────────────────────────────────

async def _process_album(media_group_id: str, message: Message):
    """Ждёт 1.5 сек после первого фото альбома, затем обрабатывает весь альбом."""
    await asyncio.sleep(1.5)
    group = album_buffer.pop(media_group_id, None)
    if not group:
        return

    photos = group["photos"]
    text = group["text"]

    # Скачиваем все фото
    logger.info("_process_album: downloading %d photo(s) for group %s", len(photos), media_group_id)
    sys.stderr.flush()
    photo_bytes_list = []
    async with aiohttp.ClientSession() as s:
        for idx, file_id in enumerate(photos):
            logger.info("_process_album: fetching file_id=%s (%d/%d)", file_id, idx + 1, len(photos))
            sys.stderr.flush()
            try:
                file = await bot.get_file(file_id)
                async with s.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}") as r:
                    data = await r.read()
                    logger.info(
                        "_process_album: downloaded file_id=%s size=%d bytes", file_id, len(data)
                    )
                    sys.stderr.flush()
                    photo_bytes_list.append(data)
            except Exception as e:
                logger.error("_process_album: failed to download file_id=%s: %s", file_id, e)
                sys.stderr.flush()

    logger.info("_process_album: downloaded %d/%d photo(s), proceeding to process_post", len(photo_bytes_list), len(photos))
    sys.stderr.flush()
    await process_post(message, text, photo_bytes_list)


@dp.message(F.photo | (F.text & ~F.text.startswith("/")))
async def handle_post(message: Message):
    if not is_me(message): return

    # Альбом (несколько фото)
    if message.media_group_id:
        mgid = message.media_group_id
        text = message.caption or ""
        file_id = message.photo[-1].file_id if message.photo else None

        if mgid not in album_buffer:
            album_buffer[mgid] = {"photos": [], "text": text}
            asyncio.create_task(_process_album(mgid, message))

        if file_id:
            album_buffer[mgid]["photos"].append(file_id)
        if text and not album_buffer[mgid]["text"]:
            album_buffer[mgid]["text"] = text
        return

    # Одиночное фото или текст
    text = message.caption or message.text or ""
    photo_bytes_list = []

    if message.photo:
        file_id = message.photo[-1].file_id
        logger.info("handle_post: downloading single photo file_id=%s", file_id)
        sys.stderr.flush()
        try:
            file = await bot.get_file(file_id)
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}") as r:
                    data = await r.read()
                    logger.info("handle_post: downloaded file_id=%s size=%d bytes", file_id, len(data))
                    sys.stderr.flush()
                    photo_bytes_list.append(data)
        except Exception as e:
            logger.error("handle_post: failed to download file_id=%s: %s", file_id, e)
            sys.stderr.flush()

    await process_post(message, text, photo_bytes_list)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    init_db()
    logger.info("Loading Buffer channels...")
    channels = await fetch_channels_from_buffer()
    if channels:
        save_channels(channels)
        logger.info("Loaded %d channels", len(channels))
    else:
        logger.warning("Could not load channels from Buffer")

    if BINANCE_API_KEY:
        stats = get_binance_queue_stats()
        logger.info(
            "Binance Square scheduler starting: %d post(s) pending in queue",
            stats["total"]
        )
        sys.stderr.flush()
        asyncio.create_task(binance_scheduler())
        logger.info("Binance Square scheduler started")
        sys.stderr.flush()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
