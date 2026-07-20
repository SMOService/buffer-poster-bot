from __future__ import annotations

import sqlite3
import time

import db as db_module


def _legacy_v4_db(path):
    """Схема v4 (до видео-колонок) с одним постом — как на проде до апгрейда."""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE binance_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, image_url TEXT,
        publish_at INTEGER NOT NULL, published INTEGER DEFAULT 0,
        created_at INTEGER DEFAULT (strftime('%s','now')),
        image_urls TEXT, content_type INTEGER DEFAULT 1, title TEXT, last_error TEXT,
        attempt_count INTEGER DEFAULT 0, image_file_ids TEXT,
        status TEXT DEFAULT 'pending', next_attempt_at INTEGER)""")
    conn.execute("CREATE TABLE channels (id TEXT PRIMARY KEY, name TEXT, service TEXT, enabled INTEGER DEFAULT 1)")
    conn.execute("CREATE TABLE published_hashes (hash TEXT PRIMARY KEY, created_at INTEGER DEFAULT (strftime('%s','now')))")
    conn.execute("INSERT INTO binance_queue (text, publish_at) VALUES ('старый пост', 1752000000)")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()


def test_migration_v4_to_v5_adds_video_columns(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_v4_db(path)
    db_module.DB_PATH = path
    db_module.init_db()
    conn = db_module.get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(binance_queue)").fetchall()]
    assert {"video_file_id", "video_cover_file_id", "video_duration"} <= set(cols)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_module.SCHEMA_VERSION
    row = db_module.get_binance_post(1)
    assert row["text"] == "старый пост"
    assert row["video_file_id"] is None


def test_video_post_roundtrip(db):
    pid = db.add_to_binance_queue(
        "видео пост", [], "2026-01-01T00:00:00Z",
        content_type=3, video_file_id="VFID", video_cover_file_id="CFID", video_duration=68,
    )
    post = db.get_binance_post(pid)
    assert post["video_file_id"] == "VFID"
    assert post["video_cover_file_id"] == "CFID"
    assert post["video_duration"] == 68
    assert post["content_type"] == 3
    # due в прошлом → пост должен подниматься планировщиком
    pending = db.get_pending_binance_posts()
    assert any(p["id"] == pid for p in pending)


def test_image_post_unaffected_by_video_fields(db):
    pid = db.add_to_binance_queue(
        "фото пост", ["http://x/1.jpg"], "2026-01-01T00:00:00Z", image_file_ids=["FID1"],
    )
    post = db.get_binance_post(pid)
    assert post["image_file_ids"] == ["FID1"]
    assert post["image_urls"] == ["http://x/1.jpg"]
    assert post["video_file_id"] is None


def test_cleanup_published_ttl(db):
    old_id = db.add_to_binance_queue("старый опубликованный", [], "2026-01-01T00:00:00Z")
    fresh_id = db.add_to_binance_queue("свежий опубликованный", [], "2026-01-01T00:00:00Z")
    keep_id = db.add_to_binance_queue("pending не трогать", [], "2026-01-01T00:00:00Z")
    late_id = db.add_to_binance_queue("создан давно, опубликован только что", [], "2026-01-01T00:00:00Z")
    db.mark_binance_published(old_id)
    db.mark_binance_published(fresh_id)
    db.mark_binance_published(late_id)
    over_ttl = int(time.time()) - 40 * 86400
    with db.get_conn() as conn:
        # TTL считается от published_at
        conn.execute("UPDATE binance_queue SET published_at=? WHERE id=?", (over_ttl, old_id))
        # created_at старый, но публикация свежая → удалять НЕЛЬЗЯ
        conn.execute("UPDATE binance_queue SET created_at=? WHERE id=?", (over_ttl, late_id))
        conn.commit()

    removed = db.cleanup_published_binance(30)
    assert removed == 1
    assert db.get_binance_post(old_id) is None
    assert db.get_binance_post(fresh_id) is not None
    assert db.get_binance_post(keep_id) is not None
    assert db.get_binance_post(late_id) is not None

    # legacy-строка без published_at (published до schema v5) → fallback на created_at
    legacy_id = db.add_to_binance_queue("legacy published", [], "2026-01-01T00:00:00Z")
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE binance_queue SET status='published', published=1, published_at=NULL, created_at=? WHERE id=?",
            (over_ttl, legacy_id),
        )
        conn.commit()
    assert db.cleanup_published_binance(30) == 1
    assert db.get_binance_post(legacy_id) is None

    # ttl=0 → выключено
    assert db.cleanup_published_binance(0) == 0


def test_claim_release_prevents_double_publish(db):
    pid = db.add_to_binance_queue("claim me", [], "2026-01-01T00:00:00Z")
    assert db.claim_binance_post(pid)           # первый публикатор захватил
    assert not db.claim_binance_post(pid)       # второй — мимо (гонка закрыта)
    assert all(p["id"] != pid for p in db.get_pending_binance_posts())  # scheduler не видит

    # quota/auth: publishing → pending c отложенной попыткой, attempt не растёт
    future = int(time.time()) + 3600
    db.release_binance_post(pid, future)
    post = db.get_binance_post(pid)
    assert post["status"] == "pending"
    assert post["next_attempt_at"] == future
    assert (post["attempt_count"] or 0) == 0

    # dead можно захватить вручную (Send Now = явный ретрай), published — нельзя
    db.mark_binance_dead(pid, "boom")
    assert db.claim_binance_post(pid)
    db.mark_binance_published(pid)
    assert not db.claim_binance_post(pid)


def test_edit_text_does_not_touch_publishing(db):
    pid = db.add_to_binance_queue("in flight", [], "2026-01-01T00:00:00Z")
    assert db.claim_binance_post(pid)
    # правка во время публикации не должна возвращать пост в pending (double-publish)
    assert not db.update_binance_text(pid, "новый текст")
    assert db.get_binance_post(pid)["status"] == "publishing"
    # после завершения публикации — правка снова работает для pending/dead
    db.mark_binance_dead(pid, "err")
    assert db.update_binance_text(pid, "новый текст")
    assert db.get_binance_post(pid)["status"] == "pending"


def test_crash_recovery_resets_publishing(db):
    pid = db.add_to_binance_queue("crashed mid-publish", [], "2026-01-01T00:00:00Z")
    assert db.claim_binance_post(pid)
    assert db.get_binance_post(pid)["status"] == "publishing"
    db.init_db()  # рестарт бота
    assert db.get_binance_post(pid)["status"] == "pending"


def test_dedup_hashes(db):
    assert not db.is_duplicate("некий текст")
    db.save_hash("некий текст")
    assert db.is_duplicate("некий текст")
    assert db.is_duplicate("  НЕКИЙ ТЕКСТ  ")  # normalize: strip + lower
    assert not db.is_duplicate("")


def test_backoff_defers_pending(db):
    pid = db.add_to_binance_queue("ретрай", [], "2026-01-01T00:00:00Z")
    future = int(time.time()) + 3600
    db.mark_binance_retry(pid, "transient error", future)
    assert all(p["id"] != pid for p in db.get_pending_binance_posts())
    post = db.get_binance_post(pid)
    assert post["attempt_count"] == 1
    assert post["last_error"] == "transient error"


def test_dead_and_revive(db):
    pid = db.add_to_binance_queue("мёртвый", [], "2026-01-01T00:00:00Z")
    db.mark_binance_dead(pid, "sensitive words")
    assert all(p["id"] != pid for p in db.get_pending_binance_posts())
    assert db.revive_binance_post(pid)
    post = db.get_binance_post(pid)
    assert post["status"] == "pending"
    assert post["attempt_count"] == 0
    assert any(p["id"] == pid for p in db.get_pending_binance_posts())
