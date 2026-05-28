from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
    force=True,
)
logger = logging.getLogger("buffer-gso")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
BUFFER_TOKEN = os.environ["BUFFER_ACCESS_TOKEN"]
BINANCE_API_KEY = os.environ.get("BINANCE_SQUARE_API_KEY", "")
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "")

SCHEDULE_MIN_HOURS = float(os.environ.get("SCHEDULE_MIN_HOURS", "1"))
SCHEDULE_MAX_HOURS = float(os.environ.get("SCHEDULE_MAX_HOURS", "240"))

BINANCE_USE_IMAGES = os.environ.get("BINANCE_USE_IMAGES", "1") not in ("0", "false", "False", "")
HISTORY_LIMIT = int(os.environ.get("HISTORY_LIMIT", "500"))

BUFFER_API = "https://api.buffer.com"
BINANCE_API_V1 = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi"
BINANCE_API_V2 = "https://www.binance.com/bapi/composite/v2/public/pgc/openApi"
BINANCE_CLIENTTYPE = "binanceSkill"

DB_PATH = Path(os.environ.get("DB_PATH", "/app/data/bot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SERVICE_EMOJI = {
    "twitter": "🐦", "linkedin": "💼", "threads": "🧵",
    "instagram": "📸", "facebook": "👤", "tiktok": "🎵",
    "mastodon": "🐘", "bluesky": "🦋", "pinterest": "📌",
}
