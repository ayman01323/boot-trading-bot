from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

from . import solana_sibot as sol
from .solana_wallet_store import SolanaWalletStore
from .user_registry import get_user, user_bool

TARGET = "Cssadv71MpgtjCyAbc77ASq1TXGvKV8AKEQmYeFFBGxZ"


def _b(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def run() -> None:
    root = Path.cwd()
    csv_dir = root / "CSVbot"
    data_dir = root / "data"
    app = SimpleNamespace(csv_dir=csv_dir, data_dir=data_dir)
    store = SolanaWalletStore(csv_dir, data_dir)
    wallet_path = csv_dir / "auto" / "solana_user_wallets.csv"
    matches = []
    if wallet_path.exists():
        with wallet_path.open("r", encoding="utf-8-sig", newline="") as f:
            matches = [r for r in csv.DictReader(f) if str(r.get("address") or "").strip() == TARGET]

    print(f"[css-live-probe] target={TARGET} found={bool(matches)}")
    for row in matches:
        tid = str(row.get("telegram_id") or "").strip()
        wid = str(row.get("wallet_id") or "").strip()
        enabled = _b(row.get("enabled"))
        active = enabled and _b(row.get("active"))
        signing = store.has_private_key(tid, wid)
        live = user_bool(csv_dir, tid, sol.SOLANA_CHAIN_ID, "solana_live_enabled", False)
        global_sibot = user_bool(csv_dir, tid, 0, "sibot_enabled", False)
        auto_sibot = user_bool(csv_dir, tid, 0, "sibot_auto_trade_enabled", False)
        user = get_user(csv_dir, tid) or {}
        account_active = str(user.get("status") or "").strip().upper() == "ACTIVE"
        can_auto = _b(user.get("can_auto_trade"))
        try:
            result = sol._rpc(app, "getBalance", [TARGET, {"commitment": "confirmed"}]) or {}
            lamports = int(result.get("value") or 0)
            balance = f"{lamports / 1_000_000_000:.9f}"
        except Exception as exc:
            balance = f"ERROR:{type(exc).__name__}"
        effective = active and signing and live and global_sibot and account_active and can_auto
        print(
            "[css-live-probe] "
            f"wallet_id={wid} active={active} signing_ready={signing} "
            f"solana_live_enabled={live} sibot_enabled={global_sibot} "
            f"sibot_auto_trade_enabled={auto_sibot} account_active={account_active} "
            f"can_auto_trade={can_auto} effective_live={effective} balance_sol={balance}"
        )


try:
    run()
except Exception as exc:
    print(f"[css-live-probe] ERROR {type(exc).__name__}: {exc}")
