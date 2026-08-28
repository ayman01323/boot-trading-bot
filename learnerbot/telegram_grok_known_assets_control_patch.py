from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from . import telegram_ui as _ui
from .user_registry import is_master

_PREV_HANDLE_UPDATE = _ui.handle_update
_DEFAULT_CONTROL_FILE = "/home/ayman01323/BOOT/testingbots/grok_known_assets_bot/grok_control.json"
_COMMANDS = {"/grokstatus", "/grokarm", "/grokstop"}


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


def _status_text() -> str:
    state = _load_state()
    return "\n".join([
        "<b>🤖 GROK KNOWN-ASSETS BOT — STATUS</b>",
        f"PAPER arm: <b>{'ARMED' if state.get('armed') else 'OFF'}</b>",
        "Market feed: <b>REAL PUBLIC DATA</b>",
        "Execution mode: <b>PAPER ONLY</b>",
        "Real-money signing: <b>DISABLED</b>",
        "Transaction broadcast: <b>DISABLED</b>",
    ])


def _handle(app, tid, cmd: str, parts: list[str]) -> None:
    if not is_master(app.csv_dir, tid):
        _ui._send(app, tid, "❌ <b>MASTER only.</b> Grok control was not changed.")
        return

    if cmd == "/grokstatus":
        _ui._send(app, tid, _status_text())
        return

    if cmd == "/grokstop":
        _save_state(armed=False, updated_by=str(tid))
        _ui._send(
            app,
            tid,
            "🛑 <b>GROK PAPER DISARMED.</b> New PAPER entries are blocked. "
            "Real-money execution remains disabled.",
        )
        return

    if len(parts) < 2:
        _ui._send(app, tid, "Use <code>/grokarm on CONFIRM</code> or <code>/grokarm off</code>.")
        return

    action = parts[1].lower()
    if action == "off":
        _save_state(armed=False, updated_by=str(tid))
        _ui._send(app, tid, "✅ <b>GROK PAPER ARM: OFF.</b> Real-money execution remains disabled.")
        return

    if action != "on" or len(parts) != 3 or parts[2].upper() != "CONFIRM":
        _ui._send(app, tid, "❌ To arm Grok use exactly: <code>/grokarm on CONFIRM</code>")
        return

    _save_state(armed=True, updated_by=str(tid))
    _ui._send(
        app,
        tid,
        "✅ <b>GROK PAPER ARMED.</b> Grok may open PAPER positions when its research and risk gates pass.\n"
        "🔒 <b>LIVE MONEY remains disabled:</b> no signing and no transaction broadcast.",
    )


def handle_update(app, update):
    message = update.get("message") or {}
    text = str(message.get("text") or "").strip()
    if not text:
        return _PREV_HANDLE_UPDATE(app, update)

    parts = text.split()
    cmd = parts[0].lower().split("@", 1)[0]
    if cmd not in _COMMANDS:
        return _PREV_HANDLE_UPDATE(app, update)

    tid = (message.get("chat") or {}).get("id")
    if tid is None:
        return
    _handle(app, tid, cmd, parts)


def install() -> None:
    if getattr(_ui, "_telegram_grok_known_assets_control_installed", False):
        return
    _ui.handle_update = handle_update
    _ui._telegram_grok_known_assets_control_installed = True
    print("[grok-known-assets-control] commands=/grokstatus,/grokarm,/grokstop mode=PAPER_ONLY")


install()
