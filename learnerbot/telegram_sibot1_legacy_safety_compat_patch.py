from __future__ import annotations

from . import telegram_sibot1_only_menu_patch as _sibot1_menu
from . import telegram_ui as _ui

_PREV = _ui.handle_update


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        data = str(cb.get("data") or "")
        # The current menu exposes only sibot1:* callbacks. Old Telegram messages
        # can still contain legacy sibot:* buttons; preserve their original
        # behaviour so an existing OFF/stop control is never neutralised by a
        # presentation-only migration.
        if data.startswith("sibot:"):
            return _sibot1_menu._PREV_HANDLE_UPDATE(app, update)
    return _PREV(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_sibot1_legacy_safety_compat_installed", False):
        return
    _ui.handle_update = handle_update
    _ui._telegram_sibot1_legacy_safety_compat_installed = True
    print("[telegram-sibot1-menu] legacy stale callbacks preserved for safety")


install()
