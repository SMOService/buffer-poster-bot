from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH, HISTORY_LIMIT

SCHEMA_VERSION = 3


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _set_user_version(conn, v: int):
    conn.execute(f"PRAGMA user_version = {int(v)}")


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

        v = _user_version(conn)
        if v < 1:
            for col, ddl in [
                ("image_urls", "ALTER TABLE binance_queue ADD COLUMN image_urls TEXT"),
                ("content_type", "ALTER TABLE binance_queue ADD COLUMN content_type INTEGER DEFAULT 1"),
                ("title", "ALTER TABLE binance_queue ADD COLUMN title TEXT"),
                ("last_error", "ALTER TABLE binance_queue ADD COLUMN last_error TEXT"),
                ("attempt_count", "ALTER TABLE binance_queue ADD COLUMN attempt_count INTEGER DEFAULT 0"),
            ]:
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            conn.execute("""CREATE TABLE IF NOT EXISTS post_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                service TEXT,
                channel_name TEXT,
                status TEXT NOT NULL,
                text_preview TEXT,
                ext_id TEXT,
                ext_url TEXT,
                error TEXT,
                created_at INTEGER DEFAULT (strftime('%s','now'))
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT
            )""")
            conn.commit()
            _set_user_version(conn, 1)
        if v < 2:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_created ON post_history(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_pending ON binance_queue(published, publish_at)")
            conn.commit()
            _set_user_version(conn, 2)
        if v < 3:
            try:
                conn.execute("ALTER TABLE binance_queue ADD COLUMN image_file_ids TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()
            _set_user_version(conn, 3)


# ── kv ────────────────────────────────────────────────────────────────────────

def kv_get(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return row["value"]


def kv_set(key: str, value):
    payload = json.dumps(value)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, payload),
        )
        conn.commit()


def is_binance_paused() -> bool:
    return bool(kv_get("binance_paused", False))


def set_binance_paused(paused: bool):
    kv_set("binance_paused", bool(paused))


# ── dedup hashes ──────────────────────────────────────────────────────────────

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


# ── channels ──────────────────────────────────────────────────────────────────

def save_channels(channels: list[dict]):
    with get_conn() as conn:
        existing = {r["id"] for r in conn.execute("SELECT id FROM channels").fetchall()}
        for ch in channels:
            if ch["id"] not in existing:
                conn.execute(
                    "INSERT INTO channels (id, name, service, enabled) VALUES (?,?,?,1)",
                    (ch["id"], ch["name"], ch["service"]),
                )
            else:
                conn.execute(
                    "UPDATE channels SET name=?, service=? WHERE id=?",
                    (ch["name"], ch["service"], ch["id"]),
                )
        conn.commit()


def get_all_channels() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM channels ORDER BY service").fetchall()]


def get_enabled_channels() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM channels WHERE enabled=1").fetchall()]


def toggle_channel(channel_id: str, enabled: bool):
    with get_conn() as conn:
        conn.execute("UPDATE channels SET enabled=? WHERE id=?", (1 if enabled else 0, channel_id))
        conn.commit()


# ── binance_queue ─────────────────────────────────────────────────────────────

def add_to_binance_queue(
    text: str,
    image_urls: list[str] | None,
    due_at_iso: str,
    *,
    content_type: int = 1,
    title: str | None = None,
    image_file_ids: list[str] | None = None,
) -> int:
    dt = datetime.fromisoformat(due_at_iso.replace("Z", "+00:00"))
    publish_at = int(dt.timestamp())
    legacy_image = image_urls[0] if image_urls else None
    image_urls_json = json.dumps(image_urls or [])
    image_file_ids_json = json.dumps(image_file_ids or [])
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO binance_queue "
            "(text, image_url, image_urls, image_file_ids, content_type, title, publish_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (text, legacy_image, image_urls_json, image_file_ids_json, content_type, title, publish_at),
        )
        conn.commit()
        return int(cur.lastrowid)


def _row_image_urls(row: dict | sqlite3.Row) -> list[str]:
    raw = row["image_urls"] if "image_urls" in row.keys() else None
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(u) for u in data if u]
        except json.JSONDecodeError:
            pass
    legacy = row["image_url"] if "image_url" in row.keys() else None
    return [legacy] if legacy else []


def _row_image_file_ids(row: dict | sqlite3.Row) -> list[str]:
    raw = row["image_file_ids"] if "image_file_ids" in row.keys() else None
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(f) for f in data if f]
    except json.JSONDecodeError:
        return []
    return []


def get_pending_binance_posts() -> list[dict]:
    now = int(datetime.now(timezone.utc).timestamp())
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM binance_queue WHERE published=0 AND publish_at<=? ORDER BY publish_at",
            (now,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["image_urls"] = _row_image_urls(r)
        d["image_file_ids"] = _row_image_file_ids(r)
        out.append(d)
    return out


def get_binance_post(post_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM binance_queue WHERE id=?", (post_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["image_urls"] = _row_image_urls(row)
    d["image_file_ids"] = _row_image_file_ids(row)
    return d


def list_binance_queue(limit: int = 10, offset: int = 0) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM binance_queue WHERE published=0 ORDER BY publish_at LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["image_urls"] = _row_image_urls(r)
        d["image_file_ids"] = _row_image_file_ids(r)
        out.append(d)
    return out


def mark_binance_published(post_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE binance_queue SET published=1 WHERE id=?", (post_id,))
        conn.commit()


def mark_binance_failed(post_id: int, error: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE binance_queue SET last_error=?, attempt_count=COALESCE(attempt_count,0)+1 WHERE id=?",
            (error[:500], post_id),
        )
        conn.commit()


def delete_binance_post(post_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM binance_queue WHERE id=?", (post_id,))
        conn.commit()
    return cur.rowcount > 0


def update_binance_text(post_id: int, text: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE binance_queue SET text=? WHERE id=? AND published=0", (text, post_id))
        conn.commit()
    return cur.rowcount > 0


def update_binance_due_at(post_id: int, publish_at_unix: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE binance_queue SET publish_at=? WHERE id=? AND published=0",
            (publish_at_unix, post_id),
        )
        conn.commit()
    return cur.rowcount > 0


def get_binance_queue_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM binance_queue WHERE published=0").fetchone()["c"]
        next_row = conn.execute(
            "SELECT publish_at FROM binance_queue WHERE published=0 ORDER BY publish_at LIMIT 1"
        ).fetchone()
        published_24h = conn.execute(
            "SELECT COUNT(*) as c FROM binance_queue WHERE published=1 AND created_at>=?",
            (int(datetime.now(timezone.utc).timestamp()) - 86400,),
        ).fetchone()["c"]
    return {
        "total": total,
        "next_at": next_row["publish_at"] if next_row else None,
        "published_24h": published_24h,
    }


# ── post_history ──────────────────────────────────────────────────────────────

def log_history(
    *,
    kind: str,
    service: str | None,
    status: str,
    text_preview: str | None,
    channel_name: str | None = None,
    ext_id: str | None = None,
    ext_url: str | None = None,
    error: str | None = None,
):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO post_history (kind, service, channel_name, status, text_preview, ext_id, ext_url, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                kind,
                service,
                channel_name,
                status,
                (text_preview or "")[:200],
                ext_id,
                ext_url,
                (error or "")[:500] if error else None,
            ),
        )
        conn.execute(
            "DELETE FROM post_history WHERE id NOT IN ("
            "SELECT id FROM post_history ORDER BY id DESC LIMIT ?)",
            (HISTORY_LIMIT,),
        )
        conn.commit()


def list_history(limit: int = 20, offset: int = 0, only_failed: bool = False) -> list[dict]:
    sql = "SELECT * FROM post_history"
    args: list = []
    if only_failed:
        sql += " WHERE status!='success'"
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def history_stats() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as ok, "
            "SUM(CASE WHEN status!='success' THEN 1 ELSE 0 END) as fail, "
            "COUNT(*) as total FROM post_history"
        ).fetchone()
    return {"ok": row["ok"] or 0, "fail": row["fail"] or 0, "total": row["total"] or 0}


def get_history_item(history_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM post_history WHERE id=?", (history_id,)).fetchone()
    return dict(row) if row else None
