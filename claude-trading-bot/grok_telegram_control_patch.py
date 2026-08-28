"""Grok known-assets Telegram control shim.

This module deliberately does NOT share Claude/SiBot arm state. It only writes
Grok's dedicated PAPER control file consumed by testingbots/grok_known_assets_bot.
It cannot enable wallet signing, transaction broadcast, or real-money execution.

Commands:
  /grokstatus
  /grokarm on CONFIRM
  /grokarm off
  /grokstop
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from learnerbot import telegram as _telegram
from learnerbot import telegram_ui as _ui

_PREV_HANDLE_UPDATE = _ui.handle_update
_DEFAULT_CONTROL_FILE = "/home/ayman01323/BOOT/testingbots/grok_known_assets_bot/grok_control.json"
_COMMANDS = {"/grokstatus", "/grokarm", "/grokstop"}


def _owner_id() -> str:
    return (
        os.environ.get("GROK_BOT_OWNER_ID", "").strip()
        or os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "").strip()
    )


def _control_path() -> Path:
    return Path(os.environ.get("GROK_CONTROL_FILE", _DEFAULT_CONTROL_FILE)).expanduser()


def _default_state() -> dict:
    return {
        "armed": False,
        "mode": "PAPER_ONLY",
        "live_money_enabled": False,
        "updated_epoch": 0,
        "updated_by": "",
    }


def _load_state() -> dict:
    state = _default_state()
    try:
        raw = json.loads(_control_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state.update(raw)
    except FileNotFoundError:
        pass
    except Exception:
        return _default_state()
    state["armed"] = bool(state.get("armed", False))
    state["mode"] = "PAPER_ONLY"
    state["live_money_enabled"] = False
    return state


def _save_state(*, armed: bool, updated_by: str) -> dict:
    target = _control_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "armed": bool(armed),
        "mode": "PAPER_ONLY",
        "live_money_enabled": False,
        "updated_epoch": int(time.time()),
        "updated_by": str(updated_by or ""),
    }
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
    return state


def _send(app, chat_id: str, text: str) -> None:
    token = str(getattr(app, "telegram_bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token or not str(chat_id).strip():
        return
    try:
        _telegram.send_message(token, str(chat_id), text, parse_mode="HTML", protect_content=True)
    except Exception as exc:
        print("[grok-telegram-control]", type(exc).__name__, str(exc)[:240])


def _require_owner(app, chat_id: str, sender_id: str) -> bool:
    owner_id = _owner_id()
    if not owner_id or str(sender_id) != owner_id:
        _send(app, chat_id, "❌ <b>Not authorised.</b> Only the configured Grok bot owner may change Grok controls.")
        return False
    return True


def _status_text() -> str:
    state = _load_state()
    armed = bool(state.get("armed"))
    return (
        "<b>🤖 GROK KNOWN-ASSETS BOT — STATUS</b>\n"
        f"PAPER arm: <b>{'ARMED' if armed else 'OFF'}</b>\n"
        "Market feed: <b>REAL PUBLIC DATA</b>\n"
        "Execution mode: <b>PAPER ONLY</b>\n"
        "Real-money signing: <b>DISABLED</b>\n"
        "Transaction broadcast: <b>DISABLED</b>\n"
    )


def _handle_grok_command(app, chat_id: str, sender_id: str, cmd: str, parts: list[str]) -> None:
    if not _require_owner(app, chat_id, sender_id):
        return

    if cmd == "/grokstatus":
        _send(app, chat_id, _status_text())
        return

    if cmd == "/grokstop":
        _save_state(armed=False, updated_by=sender_id)
        _send(
            app,
            chat_id,
            "🛑 <b>GROK PAPER DISARMED.</b> New PAPER entries are blocked. "
            "Real-money execution remains disabled.",
        )
        return

    if cmd == "/grokarm":
        if len(parts) < 2:
            _send(app, chat_id, "Use <code>/grokarm on CONFIRM</code> or <code>/grokarm off</code>.")
            return
        action = parts[1].lower()
        if action == "off":
            _save_state(armed=False, updated_by=sender_id)
            _send(app, chat_id, "✅ <b>GROK PAPER ARM: OFF.</b> Real-money execution remains disabled.")
            return
        if action != "on" or len(parts) != 3 or parts[2].upper() != "CONFIRM":
            _send(app, chat_id, "❌ To arm Grok use exactly: <code>/grokarm on CONFIRM</code>")
            return
        _save_state(armed=True, updated_by=sender_id)
        _send(
            app,
            chat_id,
            "✅ <b>GROK PAPER ARMED.</b> Grok may open PAPER positions when its research and risk gates pass.\n"
            "🔒 <b>LIVE MONEY remains disabled:</b> no signing and no transaction broadcast.",
        )
        return


def handle_update(app, update):
    message = update.get("message") or {}
    text = str(message.get("text") or "").strip()
    if not text:
        return _PREV_HANDLE_UPDATE(app, update)
    parts = text.split()
    cmd = parts[0].lower().split("@", 1)[0]
    if cmd not in _COMMANDS:
        return _PREV_HANDLE_UPDATE(app, update)

    chat_id = str((message.get("chat") or {}).get("id") or "")
    sender_id = str((message.get("from") or {}).get("id") or chat_id)
    _handle_grok_command(app, chat_id, sender_id, cmd, parts)


def install() -> None:
    if not getattr(_ui, "_grok_control_patch_installed", False):
        _ui.handle_update = handle_update
        _ui._grok_control_patch_installed = True
        print("[grok-telegram-control] installed commands=/grokstatus,/grokarm,/grokstop mode=PAPER_ONLY")
