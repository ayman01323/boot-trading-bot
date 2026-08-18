from __future__ import annotations

import time
from pathlib import Path

from . import user_registry as users

TELEGRAM_ID = "5882384847"
MARKER = ".telegram_user_5882384847_added_20260818"


def apply():
    root = Path(__file__).resolve().parent.parent
    csv_dir = root / "CSVbot"
    marker = root / "data" / MARKER
    if marker.exists():
        print("[user-migration] telegram_id=5882384847 already_applied=true")
        return

    existing_users = users.all_users(csv_dir)
    active_master = any(
        str(row.get("role") or "").upper() == "MASTER"
        and str(row.get("status") or "").upper() == "ACTIVE"
        for row in existing_users
    )
    if not active_master:
        print("[user-migration] telegram_id=5882384847 deferred=no_active_master")
        return

    row = users.get_user(csv_dir, TELEGRAM_ID)
    if row is None:
        users.join_user(csv_dir, TELEGRAM_ID, "STANDARD")

    users.update_user(
        csv_dir,
        TELEGRAM_ID,
        role="USER",
        status="ACTIVE",
        fee_plan_id="STANDARD",
        label="User 5882384847",
        allowed_chains="*",
        max_wallets="5",
        can_transfer="true",
        can_manual_trade="true",
        can_auto_trade="true",
        activated_epoch=int(time.time()),
        notes="Added by MASTER request on 2026-08-18; explicitly non-master",
    )

    # Double-check the migration can never leave this account with MASTER role.
    final = users.get_user(csv_dir, TELEGRAM_ID) or {}
    if str(final.get("role") or "").upper() != "USER":
        raise RuntimeError("non-master user migration failed role verification")
    if str(final.get("status") or "").upper() != "ACTIVE":
        raise RuntimeError("non-master user migration failed active-status verification")

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"applied={int(time.time())}\n", encoding="utf-8")
    print(
        "[user-migration] telegram_id=5882384847 role=USER status=ACTIVE "
        "plan=STANDARD allowed_chains=* max_wallets=5"
    )


try:
    apply()
except Exception as exc:
    print(f"[user-migration] telegram_id=5882384847 ERROR {type(exc).__name__}: {exc}")
