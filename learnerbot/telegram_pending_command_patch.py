from __future__ import annotations

from . import telegram_ui as _ui
from . import telegram_sibot_patch as _sibot_ui

_original_handle_update = _ui.handle_update


def handle_update(app, update):
    """Give slash commands priority over any pending SiBot numeric prompt."""
    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()

    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        pending_key = _sibot_ui._PENDING.get(str(tid))
        if pending_key:
            _sibot_ui._PENDING.pop(str(tid), None)
            if cmd == "/cancel":
                _ui._send(
                    app,
                    tid,
                    "✅ SiBot setting change cancelled.",
                    _sibot_ui.settings_keyboard(app, tid),
                )
                return

    return _original_handle_update(app, update)


def install():
    if getattr(_ui, "_pending_command_patch_installed", False):
        return
    _ui.handle_update = handle_update
    _ui._pending_command_patch_installed = True


install()
