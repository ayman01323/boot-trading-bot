from __future__ import annotations

import time

from .config import AppSettings
from .user_registry import get_user, join_user, update_user

# MASTER Telegram administrators explicitly authorised by the operator.
MASTER_ADMIN_TELEGRAM_IDS = (
    "5923828381",
    "6760898817",
)


def ensure_master_admins(app: AppSettings | None = None) -> list[str]:
    """Ensure the configured Telegram IDs exist as ACTIVE MASTER accounts.

    This only changes the local CSV user registry. It does not expose private
    keys or alter trading gates. MASTER permissions are then enforced by the
    existing Telegram role checks.
    """
    app = app or AppSettings.load()
    now = int(time.time())
    promoted: list[str] = []

    for telegram_id in MASTER_ADMIN_TELEGRAM_IDS:
        if get_user(app.csv_dir, telegram_id) is None:
            join_user(app.csv_dir, telegram_id, "MASTER")
        update_user(
            app.csv_dir,
            telegram_id,
            role="MASTER",
            status="ACTIVE",
            fee_plan_id="MASTER",
            label=f"Master Admin {telegram_id}",
            allowed_chains="*",
            max_wallets="20",
            can_transfer="true",
            can_manual_trade="true",
            can_auto_trade="true",
            activated_epoch=now,
            notes="Promoted to MASTER by authorised BOOT deployment",
        )
        promoted.append(telegram_id)

    return promoted
