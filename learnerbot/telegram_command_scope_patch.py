from __future__ import annotations

from pathlib import Path

from . import telegram as _tg
from . import telegram_ui as _ui
from .user_registry import all_users

_PREV_SET_COMMANDS = _ui.set_commands

# Commands whose handlers are MASTER-only in telegram_ui.py, plus /autodeploy.
MASTER_ONLY = {
    "control", "platformlive", "platformauto", "adminusers", "admincode", "engine",
    "setmax", "setprofit", "setcopy", "setedge", "setage", "setcanary", "setscore",
    "alerts", "copy20", "signals", "wallets", "profit", "behaviours", "rankings",
    "strategies", "report", "autodeploy",
}


def _csv_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "CSVbot"


def _dedupe(commands):
    out = []
    seen = set()
    for row in commands or []:
        cmd = str((row or {}).get("command") or "").strip().lower()
        if not cmd or cmd in seen:
            continue
        seen.add(cmd)
        out.append({"command": cmd, "description": str((row or {}).get("description") or "")[:256]})
    return out


def set_commands(token: str):
    # Let every earlier feature patch contribute its commands first, then scope
    # the final union by role. This keeps SiBot/user commands while removing
    # MASTER commands from the normal blue Telegram command icon.
    _PREV_SET_COMMANDS(token)
    current = _dedupe(_tg._json("getMyCommands", token, payload={}, timeout=15) or [])
    if not any(x["command"] == "autodeploy" for x in current):
        current.append({"command": "autodeploy", "description": "MASTER deployment status"})

    user_commands = [
        x for x in current
        if x["command"] not in MASTER_ONLY and "MASTER" not in x["description"].upper()
    ]
    master_commands = _dedupe(current)

    # Safe default for any chat not explicitly known to the registry.
    _tg._json(
        "setMyCommands", token,
        payload={"commands": user_commands, "scope": {"type": "default"}},
        timeout=15,
    )
    _tg._json(
        "setMyCommands", token,
        payload={"commands": user_commands, "scope": {"type": "all_private_chats"}},
        timeout=15,
    )

    masters = 0
    users = 0
    for row in all_users(_csv_dir()):
        tid = str(row.get("telegram_id") or "").strip()
        if not tid or not tid.lstrip("-").isdigit():
            continue
        status = str(row.get("status") or "").upper()
        if status == "SUSPENDED":
            continue
        is_master = str(row.get("role") or "USER").upper() == "MASTER"
        scoped = master_commands if is_master else user_commands
        _tg._json(
            "setMyCommands", token,
            payload={
                "commands": scoped,
                "scope": {"type": "chat", "chat_id": int(tid)},
            },
            timeout=15,
        )
        if is_master:
            masters += 1
        else:
            users += 1

    print(
        f"[telegram-command-scope] user_commands={len(user_commands)} "
        f"master_commands={len(master_commands)} masters={masters} users={users}"
    )


def install():
    if getattr(_ui, "_telegram_command_scope_patch_installed", False):
        return
    _ui.set_commands = set_commands
    _ui._telegram_command_scope_patch_installed = True


install()
