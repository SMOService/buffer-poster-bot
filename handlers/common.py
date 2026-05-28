from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from aiogram.types import CallbackQuery, Message

from config import ALLOWED_USER_ID, SCHEDULE_MAX_HOURS, SCHEDULE_MIN_HOURS


def is_me(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == ALLOWED_USER_ID


def is_me_cb(call: CallbackQuery) -> bool:
    return call.from_user is not None and call.from_user.id == ALLOWED_USER_ID


def random_due_at_iso() -> str:
    offset = random.randint(int(SCHEDULE_MIN_HOURS * 3600), int(SCHEDULE_MAX_HOURS * 3600))
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fmt_due_iso(due_at: str) -> str:
    dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    return dt.strftime("%d %b %Y %H:%M UTC")


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")


def fmt_delta(ts: int, now: int | None = None) -> str:
    """'(через 12ч 30м)' / '(просрочен на 5м)'."""
    if now is None:
        now = int(datetime.now(timezone.utc).timestamp())
    delta = ts - now
    if delta >= 0:
        h, m = divmod(delta // 60, 60)
        return f"через {h}ч {m}м" if h else f"через {m}м"
    delta = -delta
    h, m = divmod(delta // 60, 60)
    return f"просрочен на {h}ч {m}м" if h else f"просрочен на {m}м"


def preview(text: str | None, n: int = 80) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"
