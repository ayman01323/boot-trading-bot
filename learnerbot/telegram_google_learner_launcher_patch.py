from __future__ import annotations

"""MASTER-menu launcher for the isolated Google learner Telegram bot.

This patch is presentation/routing only.  It deliberately does *not* accept or
persist a learner private key inside the production Telegram process.  The key
import must happen in the dedicated learner bot running from the Google learner
instance so production and learner wallet stores cannot be mixed accidentally.
"""

import html
import os
import re

from . import telegram as _tg
from . import telegram_ui as _ui

_PREV_MENU = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update
_CALLBACKS = {"learnergoogle:home", "learnergoogle:refresh"}


def _username() -> str:
    raw = str(
        os.getenv("LEARNER_TELEGRAM_USERNAME")
        or os.getenv("LEARNER_BOT_USERNAME")
        or ""
    ).strip().lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", raw or ""):
        return raw
    return ""


def _button_exists(rows) -> bool:
    for row in rows:
        for button in row:
            if str(button.get("callback_data") or "") == "learnergoogle:home":
                return True
            if str(button.get("text") or "").startswith("🧠 Learner Bot"):
                return True
    return False


def menu_keyboard(app=None, chat_id=None):
    keyboard = _PREV_MENU(app, chat_id)
    rows = list(keyboard.get("inline_keyboard") or [])
    if app is None or chat_id is None:
        return {"inline_keyboard": rows}
    try:
        if not _ui._master(app, chat_id):
            return {"inline_keyboard": rows}
    except Exception:
        return {"inline_keyboard": rows}

    if _button_exists(rows):
        return {"inline_keyboard": rows}

    row = [{"text": "🧠 Learner Bot — Google Test", "callback_data": "learnergoogle:home"}]
    insert_at = 1
    for i, existing in enumerate(rows):
        if any(str(b.get("callback_data") or "") == "sr:home" for b in existing):
            insert_at = i + 1
            break
    rows.insert(min(insert_at, len(rows)), row)
    return {"inline_keyboard": rows}


def learner_page() -> str:
    username = _username()
    state = (
        f"🟢 Dedicated Telegram: <b>@{html.escape(username)}</b>"
        if username
        else "🟡 Dedicated Telegram: <b>username not configured yet</b>"
    )
    return "\n".join([
        "<b>🧠 LEARNER BOT — GOOGLE TEST</b>",
        "🔒 <b>INSTANCE:</b> LEARNER ONLY • <b>SERVER:</b> botgoogle",
        "⚠️ <b>NOT THE PRODUCTION BOT</b>",
        "━━━━━━━━━━━━",
        "",
        state,
        "Google learner path:",
        "<code>/home/ayman01323/BOOT/testingbots/learn</code>",
        "",
        "<b>Private-key rule</b>",
        "The production bot does not store or relay learner private keys.",
        "Open the dedicated learner Telegram bot, then tap <b>🔐 Add Private Key</b> there.",
        "This keeps each Telegram user's learner wallet isolated from production wallets.",
    ])


def learner_keyboard() -> dict:
    username = _username()
    rows = []
    if username:
        rows.append([
            {
                "text": "🔐 Open Learner & Add Private Key",
                "url": f"https://t.me/{username}?start=learner",
            }
        ])
    else:
        rows.append([
            {
                "text": "🟡 Learner Telegram not connected",
                "callback_data": "learnergoogle:refresh",
            }
        ])
    rows.extend([
        [{"text": "🔄 Refresh", "callback_data": "learnergoogle:refresh"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ])
    return {"inline_keyboard": rows}


def _answer(app, cb, text="") -> None:
    qid = str((cb or {}).get("id") or "")
    if not qid:
        return
    try:
        _tg.answer_callback_query(app.telegram_bot_token, qid, text)
    except Exception:
        pass


def handle_update(app, update):
    cb = update.get("callback_query") or {}
    data = str(cb.get("data") or "")
    tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
    if tid is not None and data in _CALLBACKS:
        try:
            if not _ui._master(app, tid):
                _answer(app, cb, "MASTER only")
                return
        except Exception:
            _answer(app, cb, "MASTER only")
            return
        _answer(app, cb, "Refreshed" if data.endswith("refresh") else "")
        _ui._send(app, tid, learner_page(), learner_keyboard())
        return

    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in {"/learnergoogle", "/learnerbot"}:
            try:
                if not _ui._master(app, tid):
                    _ui._send(app, tid, "MASTER only.", _ui.back_keyboard())
                    return
            except Exception:
                _ui._send(app, tid, "MASTER only.", _ui.back_keyboard())
                return
            _ui._send(app, tid, learner_page(), learner_keyboard())
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_google_learner_launcher_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._google_learner_launcher_installed = True


install()
