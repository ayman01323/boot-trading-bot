from __future__ import annotations

import copy

from . import telegram_ui as _ui
from .user_registry import is_master

_PREV_MENU = _ui.menu_keyboard

_USER_ROWS = [
    [
        {"text": "🤖 SiBot", "callback_data": "menu:sibot"},
        {"text": "💰 Capital", "callback_data": "menu:capital"},
    ],
    [
        {"text": "🔐 Wallets", "callback_data": "menu:wallet"},
        {"text": "💱 Trading", "callback_data": "menu:trading"},
    ],
    [
        {"text": "⚡ Auto", "callback_data": "menu:auto"},
        {"text": "🛰 Opportunities", "callback_data": "menu:opportunities"},
    ],
    [
        {"text": "📡 Status", "callback_data": "menu:status"},
        {"text": "❓ Help", "callback_data": "menu:help"},
    ],
]


def menu_keyboard(app=None, chat_id=None):
    """Keep the full MASTER menu, but render a deliberately short USER menu."""
    if app is not None and chat_id is not None:
        try:
            if not is_master(app.csv_dir, chat_id):
                return {"inline_keyboard": copy.deepcopy(_USER_ROWS)}
        except Exception:
            # If role lookup cannot be completed, preserve the existing menu rather
            # than accidentally treating an unknown chat as an authorised USER.
            pass
    return _PREV_MENU(app, chat_id)


def install():
    if getattr(_ui, "_user_menu_compact_patch_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui._user_menu_compact_patch_installed = True


install()
