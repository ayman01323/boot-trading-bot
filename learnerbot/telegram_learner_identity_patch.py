from __future__ import annotations

"""Visible identity guard for the isolated Google learner Telegram bot.

This patch is deliberately presentation-only. It makes the learner unmistakable in
Telegram without changing wallets, strategy, risk controls or execution state.
"""

import html
import os
from pathlib import Path

from . import telegram as _tg
from . import telegram_learner_only_menu_patch as _menu
from . import telegram_ui as _ui

_TARGET = Path("/home/ayman01323/BOOT/testingbots/learn")
_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_HOME = _menu.home_text
_PREV_STRATEGY = _menu._strategy_page
_PREV_SETTINGS = _menu._settings_page
_PREV_RESULTS = _menu._results_page
_PREV_RISK = _menu._risk_page
_PREV_STATUS = _menu._status_page

_BRAND = "🧠 <b>LEARNER BOT — GOOGLE TEST</b>"
_INSTANCE = "🔒 <b>INSTANCE:</b> LEARNER ONLY • <b>SERVER:</b> botgoogle"


def _enabled() -> bool:
    env = str(os.getenv("LEARNER_ONLY_TELEGRAM", "")).strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    try:
        return Path(__file__).resolve().parents[1] == _TARGET
    except Exception:
        return False


def _brand(text: str) -> str:
    body = str(text or "").strip()
    # Avoid repeating the learner title already present on the home page.
    if body.startswith("<b>🧠 LEARNER BOT</b>"):
        lines = body.splitlines()
        body = "\n".join(lines[2:]).lstrip() if len(lines) > 2 else ""
    return "\n".join([
        _BRAND,
        _INSTANCE,
        "⚠️ <b>NOT THE PRODUCTION BOT</b>",
        "━━━━━━━━━━━━",
        "",
        body,
    ]).strip()


def home_text():
    return _brand(_PREV_HOME())


def strategy_page(app, tid):
    return _brand(_PREV_STRATEGY(app, tid))


def settings_page(app, tid):
    return _brand(_PREV_SETTINGS(app, tid))


def results_page(app, tid):
    return _brand(_PREV_RESULTS(app, tid))


def risk_page(app, tid):
    return _brand(_PREV_RISK(app, tid))


def status_page(app, tid):
    return _brand(_PREV_STATUS(app, tid))


def identity_text() -> str:
    return "\n".join([
        _BRAND,
        _INSTANCE,
        "⚠️ <b>NOT THE PRODUCTION BOT</b>",
        "",
        "Purpose: isolated Solana learner and 17-August strategy testing.",
        "Server path:",
        "<code>/home/ayman01323/BOOT/testingbots/learn</code>",
        "",
        "Use this bot only for learner wallets and learner testing.",
    ])


def learner_set_commands(token: str):
    # Telegram profile identity. Failure of cosmetic profile updates must never
    # affect the learner runtime; the command menu is still attempted below.
    for method, payload in [
        ("setMyName", {"name": "Learner Bot — Google Test"}),
        ("setMyShortDescription", {"short_description": "Isolated Solana learner • Google test • NOT production"}),
        ("setMyDescription", {"description": "Isolated Solana learner bot on the Google test server. Used only for learner wallets and restored 17-August strategy testing. This is NOT the production trading bot."}),
    ]:
        try:
            _tg._json(method, token, payload=payload, timeout=15)
        except Exception as exc:
            print("[learner-telegram-identity]", method, type(exc).__name__, exc)

    commands = [
        {"command": "menu", "description": "Open Learner Bot menu"},
        {"command": "instance", "description": "Confirm this is the Google learner"},
        {"command": "join", "description": "Register with the Learner Bot"},
        {"command": "activate", "description": "Activate Learner account with code"},
        {"command": "solwallet", "description": "My learner Solana wallets"},
        {"command": "solwalletimport", "description": "Import learner Solana signing key"},
        {"command": "learner", "description": "Open Learner dashboard"},
    ]
    _tg._json("setMyCommands", token, payload={"commands": commands}, timeout=15)


def handle_update(app, update):
    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
    if tid is not None and cmd == "/instance":
        try:
            if _ui._auth(app, tid):
                _ui._send(app, tid, identity_text(), _menu.learner_menu_keyboard(app, tid))
                return True
        except Exception as exc:
            _ui._send(app, tid, f"❌ <b>Learner identity error</b>\n<code>{html.escape(str(exc)[:400])}</code>")
            return True
    return _PREV_HANDLE_UPDATE(app, update)


def install():
    if not _enabled():
        return
    if getattr(_ui, "_learner_identity_patch_installed", False):
        return

    # The learner menu calls these module functions directly, so replace both
    # the module bindings and the public telegram_ui hooks.
    _menu.home_text = home_text
    _menu._strategy_page = strategy_page
    _menu._settings_page = settings_page
    _menu._results_page = results_page
    _menu._risk_page = risk_page
    _menu._status_page = status_page
    _menu.learner_set_commands = learner_set_commands

    _ui.home_text = home_text
    _ui.set_commands = learner_set_commands
    _ui.handle_update = handle_update
    _ui._learner_identity_patch_installed = True


install()
