from __future__ import annotations

import time
from pathlib import Path

from . import solana_sibot as _sol
from .user_registry import all_users, set_user_setting

MARKER = ".solana_emergency_loss_halt_20260818_v1"


def apply():
    root = Path(__file__).resolve().parent.parent
    marker = root / "data" / MARKER
    if marker.exists():
        print("[solana-emergency-halt] already_applied=true")
        return

    disabled = 0
    for user in all_users(root / "CSVbot", enabled_only=False):
        tid = str(user.get("telegram_id") or "").strip()
        if not tid:
            continue
        try:
            set_user_setting(
                root / "CSVbot",
                tid,
                "solana_live_enabled",
                "false",
                chain_id=_sol.SOLANA_CHAIN_ID,
                description="Emergency one-shot halt after repeated Solana loss/exit-retry pattern",
            )
            disabled += 1
        except Exception as exc:
            print("[solana-emergency-halt:user]", tid, type(exc).__name__, exc)

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"applied_epoch={int(time.time())}\ndisabled_users={disabled}\nreason=repeated_loss_and_exit_retry_pattern\n",
        encoding="utf-8",
    )
    print(f"[solana-emergency-halt] disabled_users={disabled} rearm_required=true")


try:
    apply()
except Exception as exc:
    print(f"[solana-emergency-halt] ERROR {type(exc).__name__}: {exc}")
