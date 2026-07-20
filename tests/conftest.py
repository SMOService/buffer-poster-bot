"""Тестовое окружение: env-заглушки ДО импорта config/db + чистая БД на каждый тест."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("BUFFER_ACCESS_TOKEN", "test-buffer-token")
os.environ.setdefault("DB_PATH", "/tmp/buffer-poster-test-bootstrap.db")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import db as db_module  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """db-модуль, переключённый на свежую БД в tmp_path."""
    db_module.DB_PATH = tmp_path / "bot.db"
    db_module.init_db()
    return db_module
