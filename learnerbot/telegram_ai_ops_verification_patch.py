from __future__ import annotations

from pathlib import Path

from . import telegram as _tg
from . import telegram_ui as _ui
from .user_registry import all_users

_PREV_SET_COMMANDS = _ui.set_commands
_EXPECTED = {"aiaudit", "aidecision", "aistrategy", "aiupdates", "aichange", "aicost"}


def _active_master_ids(csv_dir: Path) -> list[str]:
    out = []
    for row in all_users(Path(csv_dir)):
        tid = str(row.get("telegram_id") or "").strip()
        if not tid or not tid.lstrip("-").isdigit():
            continue
        if str(row.get("role") or "USER").upper() != "MASTER":
            continue
        if str(row.get("status") or "").upper() != "ACTIVE":
            continue
        if tid not in out:
            out.append(tid)
    return out


def set_commands_verified(token: str):
    _PREV_SET_COMMANDS(token)
    try:
        csv_dir = Path(__file__).resolve().parents[1] / "CSVbot"
        masters = _active_master_ids(csv_dir)
        missing_by_master = {}
        for tid in masters:
            scope = {"type": "chat", "chat_id": int(tid)}
            rows = _tg._json("getMyCommands", token, payload={"scope": scope}, timeout=15) or []
            names = {str((row or {}).get("command") or "").strip().lower() for row in rows}
            missing = sorted(_EXPECTED - names)
            if missing:
                missing_by_master[tid] = missing
        verified = bool(masters) and not missing_by_master
        missing_text = ",".join(
            f"{tid}:{'|'.join(items)}" for tid, items in sorted(missing_by_master.items())
        ) or "none"
        print(
            "[telegram-ai-ops-commands] "
            f"verified={'true' if verified else 'false'} "
            f"masters={len(masters)} expected={','.join(sorted(_EXPECTED))} missing={missing_text}"
        )
    except Exception as exc:
        print(f"[telegram-ai-ops-commands] verified=false error={type(exc).__name__}:{str(exc)[:300]}")


def install():
    if getattr(_ui, "_telegram_ai_ops_verification_patch_installed", False):
        return
    _ui.set_commands = set_commands_verified
    _ui._telegram_ai_ops_verification_patch_installed = True


install()
