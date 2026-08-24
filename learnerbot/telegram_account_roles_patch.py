from __future__ import annotations

import csv
import sqlite3
import time
from pathlib import Path

from . import cli as _cli
from .multi_wallet_store import MultiWalletStore
from .solana_wallet_store import SolanaWalletStore
from .user_registry import get_user, join_user, update_user, user_bool, user_setting

MAIN_MASTER_ID = "5923828381"
OTHER_MASTER_IDS = {"6760898817"}
NON_MASTER_IDS = {"5882384847", "461513364"}
MARKER = ".telegram_roles_20260818_v1"
_PREV_APP = _cli._app


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _ensure_roles(app):
    marker = Path(app.data_dir) / MARKER
    if marker.exists():
        return

    # Existing masters are not silently created or activated. We only repair the
    # role when the account already exists, preserving status and every trading gate.
    for tid, label in ((MAIN_MASTER_ID, "Main Master"), ("6760898817", "Master 6760898817")):
        row = get_user(app.csv_dir, tid)
        if row:
            update_user(app.csv_dir, tid, role="MASTER", label=label)

    # 5882384847 was explicitly requested as a new non-master account.
    row = get_user(app.csv_dir, "5882384847")
    if row is None:
        join_user(app.csv_dir, "5882384847", "STANDARD")
        update_user(
            app.csv_dir,
            "5882384847",
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
            notes="Added by main master request on 2026-08-18; non-master",
        )
    else:
        # Preserve all existing trading settings and switches; only role is corrected.
        update_user(app.csv_dir, "5882384847", role="USER", label="User 5882384847")

    # 461513364 is corrected to USER only if it already exists. Its status and
    # trading settings are deliberately untouched.
    row = get_user(app.csv_dir, "461513364")
    if row:
        update_user(app.csv_dir, "461513364", role="USER", label="User 461513364")

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"applied={int(time.time())}\n", encoding="utf-8")
    print("[telegram-roles] applied main_master=5923828381 other_master=6760898817 users=5882384847,461513364 mirroring=false")


def _count_recent_evm(app, tid: str, since: int) -> int:
    path = Path(app.csv_dir) / "auto" / "auto_trade_execution.csv"
    if not path.exists():
        return 0
    n = 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if str(row.get("telegram_id") or "").strip() != tid:
                    continue
                try:
                    ts = int(float(row.get("timestamp_epoch") or 0))
                except Exception:
                    ts = 0
                if ts < since:
                    continue
                if str(row.get("status") or "").upper() in {"SUCCESS", "SUCCESS_FEE_PENDING", "BROADCAST"}:
                    n += 1
    except Exception:
        return 0
    return n


def _count_recent_sol(app, tid: str, since: int) -> tuple[int, int]:
    path = Path(app.data_dir) / "solana_sibot.sqlite3"
    if not path.exists():
        return 0, 0
    try:
        conn = sqlite3.connect(path, timeout=2.0)
        try:
            buys = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE telegram_id=? AND mode='LIVE' AND entry_ts>=?",
                (tid, since),
            ).fetchone()[0]
            sells = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE telegram_id=? AND mode='LIVE' AND status='CLOSED' AND COALESCE(closed_at,0)>=?",
                (tid, since),
            ).fetchone()[0]
            return int(buys or 0), int(sells or 0)
        finally:
            conn.close()
    except Exception:
        return 0, 0


def _audit(app):
    now = int(time.time())
    since = now - 86400
    evm_store = MultiWalletStore(app.data_dir, app.csv_dir)
    sol_store = SolanaWalletStore(app.csv_dir, app.data_dir)
    for tid in (MAIN_MASTER_ID, "6760898817", "5882384847", "461513364"):
        row = get_user(app.csv_dir, tid)
        if not row:
            print(f"[telegram-account] tid={tid} registered=false")
            continue
        try:
            em = evm_store.get_meta(tid)
            evm_wallet = True
            evm_signing_file = evm_store._wallet_file(tid, em.get("wallet_id")).exists()
        except Exception:
            evm_wallet = False
            evm_signing_file = False
        try:
            sm = sol_store.get_meta(tid)
            sol_wallet = True
            sol_signing = sol_store.has_private_key(tid, sm.get("wallet_id"))
        except Exception:
            sol_wallet = False
            sol_signing = False
        sol_live = user_bool(app.csv_dir, tid, -101, "solana_live_enabled", False)
        sibot_on = _bool(user_setting(app.csv_dir, tid, 0, "sibot_enabled", "false"), False)
        sibot_auto = _bool(user_setting(app.csv_dir, tid, 0, "sibot_auto_trade_enabled", "false"), False)
        evm_24h = _count_recent_evm(app, tid, since)
        sol_buys, sol_sells = _count_recent_sol(app, tid, since)
        print(
            f"[telegram-account] tid={tid} role={row.get('role')} status={row.get('status')} "
            f"can_auto={row.get('can_auto_trade')} evm_wallet={str(evm_wallet).lower()} "
            f"evm_signing_file={str(evm_signing_file).lower()} sol_wallet={str(sol_wallet).lower()} "
            f"sol_signing={str(sol_signing).lower()} sol_live={str(sol_live).lower()} "
            f"sibot_on={str(sibot_on).lower()} sibot_auto={str(sibot_auto).lower()} "
            f"evm_exec_24h={evm_24h} sol_buy_24h={sol_buys} sol_sell_24h={sol_sells} mirroring=false"
        )


def _app_with_roles():
    app = _PREV_APP()
    try:
        _ensure_roles(app)
        _audit(app)
    except Exception as exc:
        print(f"[telegram-roles] ERROR {type(exc).__name__}: {exc}")
    return app


_cli._app = _app_with_roles

# MASTER-only AI operations reporting composes on top of the established role wrapper.
# It reads only sanitised ai-reviews state and never changes trading hooks or wallet data.
from . import telegram_ai_ops_patch  # noqa: E402,F401
# MASTER-only central report schedule control composes after AI status handlers.
from . import telegram_report_schedule_patch  # noqa: E402,F401
# Verify the final chat-scoped AI command registration against Telegram itself.
from . import telegram_ai_ops_verification_patch  # noqa: E402,F401
