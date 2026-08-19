from __future__ import annotations

from pathlib import Path

from . import telegram as _tg
from . import telegram_sibot_patch as _sibot_ui
from . import telegram_ui as _ui
from . import telegram_user_menu_compact_patch as _reports_ui
from .user_registry import all_users

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_SET_COMMANDS = _ui.set_commands


def _csv_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "CSVbot"


def _with_reports_command(commands):
    out = []
    seen = set()
    for row in commands or []:
        cmd = str((row or {}).get("command") or "").strip().lower()
        if not cmd or cmd in seen:
            continue
        seen.add(cmd)
        out.append({
            "command": cmd,
            "description": str((row or {}).get("description") or "")[:256],
        })
    if "reports" not in seen:
        out.append({"command": "reports", "description": "My reports and loss alerts"})
    return out[:100]


def set_commands(token: str):
    """Keep existing command scoping and add /reports to registered active chats."""
    _PREV_SET_COMMANDS(token)
    try:
        for row in all_users(_csv_dir()):
            tid = str(row.get("telegram_id") or "").strip()
            status = str(row.get("status") or "").upper()
            if not tid or not tid.lstrip("-").isdigit() or status != "ACTIVE":
                continue
            scope = {"type": "chat", "chat_id": int(tid)}
            current = _tg._json(
                "getMyCommands",
                token,
                payload={"scope": scope},
                timeout=15,
            ) or []
            _tg._json(
                "setMyCommands",
                token,
                payload={"commands": _with_reports_command(current), "scope": scope},
                timeout=15,
            )
    except Exception as exc:
        print(f"[telegram-reports-command] {type(exc).__name__}: {exc}")


def handle_update(app, update):
    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()

    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in {"/reports", "/myalerts"}:
            if not _ui._auth(app, tid):
                return
            # A navigation command must never be swallowed by an older numeric prompt.
            _reports_ui._PENDING.pop(str(tid), None)
            _sibot_ui._PENDING.pop(str(tid), None)
            _ui._send(
                app,
                tid,
                _reports_ui.alerts_page(app, tid),
                _reports_ui.alerts_keyboard(app, tid),
            )
            return

    return _PREV_HANDLE_UPDATE(app, update)


def install():
    if getattr(_ui, "_telegram_reports_direct_command_installed", False):
        return
    _ui.handle_update = handle_update
    _ui.set_commands = set_commands
    _ui._telegram_reports_direct_command_installed = True


install()
