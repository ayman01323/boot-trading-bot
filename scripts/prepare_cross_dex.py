#!/usr/bin/env python3
from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from web3 import Web3

from learnerbot.config import AppSettings, load_kv_scoped
from learnerbot.live_executor import LiveTrader
from learnerbot.multi_wallet_store import MultiWalletStore


def main():
    ap = argparse.ArgumentParser(description="Prepare one user's wallet for atomic cross-DEX V2 execution.")
    ap.add_argument("--telegram-id", required=True)
    ap.add_argument("--chain", required=True, help="bsc, base, ethereum, arbitrum or polygon")
    ap.add_argument("--amount", required=True, type=Decimal, help="Exact wrapped-base allowance cap")
    args = ap.parse_args()

    if args.amount <= 0:
        ap.error("--amount must be positive")

    app = AppSettings.load()
    store = MultiWalletStore(app.data_dir, app.csv_dir)
    meta = store.get_meta(str(args.telegram_id))
    trader = LiveTrader(app, args.chain, telegram_id=str(args.telegram_id), wallet_id=meta["wallet_id"])
    cfg = load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", trader.chain.chain_id)
    executor = str(cfg.get("cross_dex_atomic_executor_address") or "").strip()
    if not Web3.is_address(executor):
        raise SystemExit("cross_dex_atomic_executor_address is not configured for this chain")
    executor = Web3.to_checksum_address(executor)
    if not trader.w3.eth.get_code(executor):
        raise SystemExit("configured cross-DEX executor address has no contract code")

    current = trader.wrapped_balance()
    wrap_hash = None
    if current < args.amount:
        wrap_hash = trader.wrap_native(args.amount - current, "CONFIRM")["tx_hash"]
        receipt = trader.w3.eth.wait_for_transaction_receipt(wrap_hash, timeout=120, poll_latency=2)
        if int(receipt.status) != 1:
            raise SystemExit(f"wrapped-native funding failed: {wrap_hash}")

    approval = trader.approve_wrapped_cap_for(executor, args.amount, "CONFIRM")
    print({
        "chain": trader.chain.slug,
        "wallet": trader.address,
        "executor": executor,
        "prepared_amount": str(args.amount),
        "wrap_hash": wrap_hash,
        "approval_hash": approval.get("approval_hash"),
    })


if __name__ == "__main__":
    main()
