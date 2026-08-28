from __future__ import annotations

from pathlib import Path

from . import telegram as _tg
from . import telegram_ui as _ui
from .user_registry import all_users

_PREV_SET_COMMANDS = _ui.set_commands

# Commands whose handlers are MASTER-only in telegram_ui.py, plus bounded
# operator controls installed by later patches.
MASTER_ONLY = {
    "control", "platformlive", "platformauto", "adminusers", "admincode", "engine",
    "setmax", "setprofit", "setcopy", "setedge", "setage", "setcanary", "setscore",
    "alerts", "copy20", "signals", "wallets", "profit", "behaviours", "rankings",
    "strategies", "report", "autodeploy", "grokstatus", "grokarm", "grokstop",
}

# The blue Telegram command sheet is navigation, not the feature surface.
# ACTIVE normal users use /menu and then the compact inline dashboard. Other
# handlers remain callable when typed manually; they are merely hidden here.
ACTIVE_USER_VISIBLE = ("menu",)
ONBOARDING_VISIBLE = ("menu", "join", "activate", "fees")
DEFAULT_VISIBLE = ("menu", "join", "activate")


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


def _visible(commands, names):
    by_name = {x["command"]: x for x in _dedupe(commands)}
    defaults = {
        "menu": "Open main menu",
        "join": "Register Telegram ID",
        "activate": "Activate account with code",
        "fees": "Show fee plan/status",
    }
    out = []
    for name in names:
        row = by_name.get(name)
        if row is None:
            row = {"command": name, "description": defaults[name]}
        out.append(row)
    return out


def set_commands(token: str):
    # Let every earlier feature patch contribute its commands first. MASTER
    # retains that full union. The normal-user blue menu is intentionally much
    # smaller than the command handlers available to the user.
    _PREV_SET_COMMANDS(token)
    current = _dedupe(_tg._json("getMyCommands", token, payload={}, timeout=15) or [])
    additions = {
        "autodeploy": "MASTER deployment status",
        "grokstatus": "MASTER Grok PAPER status",
        "grokarm": "MASTER Grok PAPER arm control",
        "grokstop": "MASTER Grok PAPER stop",
    }
    present = {x["command"] for x in current}
    for command, description in additions.items():
        if command not in present:
            current.append({"command": command, "description": description})

    non_master_commands = [
        x for x in current
        if x["command"] not in MASTER_ONLY and "MASTER" not in x["description"].upper()
    ]
    active_user_commands = _visible(non_master_commands, ACTIVE_USER_VISIBLE)
    onboarding_commands = _visible(non_master_commands, ONBOARDING_VISIBLE)
    default_commands = _visible(non_master_commands, DEFAULT_VISIBLE)
    master_commands = _dedupe(current)

    # Unknown/unregistered private chats need only onboarding commands.
    _tg._json(
        "setMyCommands", token,
        payload={"commands": default_commands, "scope": {"type": "default"}},
        timeout=15,
    )
    _tg._json(
        "setMyCommands", token,
        payload={"commands": default_commands, "scope": {"type": "all_private_chats"}},
        timeout=15,
    )

    masters = 0
    active_users = 0
    onboarding_users = 0
    for row in all_users(_csv_dir()):
        tid = str(row.get("telegram_id") or "").strip()
        if not tid or not tid.lstrip("-").isdigit():
            continue
        status = str(row.get("status") or "").upper()
        if status == "SUSPENDED":
            continue
        is_master = str(row.get("role") or "USER").upper() == "MASTER"
        if is_master:
            scoped = master_commands
            masters += 1
        elif status == "ACTIVE":
            scoped = active_user_commands
            active_users += 1
        else:
            scoped = onboarding_commands
            onboarding_users += 1
        _tg._json(
            "setMyCommands", token,
            payload={
                "commands": scoped,
                "scope": {"type": "chat", "chat_id": int(tid)},
            },
            timeout=15,
        )

    print(
        f"[telegram-command-scope] active_user_commands={len(active_user_commands)} "
        f"onboarding_commands={len(onboarding_commands)} "
        f"master_commands={len(master_commands)} masters={masters} "
        f"active_users={active_users} onboarding_users={onboarding_users}"
    )


def install():
    if getattr(_ui, "_telegram_command_scope_patch_installed", False):
        return
    _ui.set_commands = set_commands
    _ui._telegram_command_scope_patch_installed = True


install()

# Legacy compatibility layer is installed first, then the final DeepSeek patch
# expands the presentation and health status to five independent reviewers.
from . import telegram_four_agent_strategy_patch  # noqa: E402,F401
from . import telegram_five_agent_patch  # noqa: E402,F401
# Add Grok as a sixth independent AI Council member after the existing five-agent
# presentation has installed. Grok remains advisory and inherits the same safety gates.
from . import telegram_grok_council_patch  # noqa: E402,F401
# Add Kimi as the seventh independent strategy reviewer after the Grok layer.
# Kimi remains advisory and does not change deterministic LIVE/risk/capital gates.
from . import telegram_kimi_seventh_review_patch  # noqa: E402,F401
# Recovery health state is loaded after the review presentation so verified
# in-progress Copilot work is shown as WAITING rather than falsely broken.
from . import ai_recovery_health_patch  # noqa: E402,F401
# MASTER Telegram can dispatch the already-bounded DeepSeek GitHub/VPS workflows.
from . import telegram_deepseek_control_patch  # noqa: E402,F401
# Grok known-assets has a completely separate PAPER arm state. These commands do
# not touch Claude/SiBot ARM/LIVE controls and cannot enable transaction broadcast.
from . import telegram_grok_known_assets_control_patch  # noqa: E402,F401
# Make the Auto Updates category control state-aware: ON can be tapped OFF, OFF can be tapped ON.
from . import telegram_auto_updates_category_toggle_patch  # noqa: E402,F401
