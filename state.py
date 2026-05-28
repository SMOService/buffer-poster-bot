from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class EditBinance(StatesGroup):
    waiting_text = State()
