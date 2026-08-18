#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# When this file is executed directly, Python places ``scripts/`` rather than the
# repository root on sys.path. Add the project root explicitly so the sibling
# ``learnerbot`` package resolves from an ordinary VPS shell invocation such as:
#   python3 scripts/export_all_user_transactions.py --hours 2 --send-telegram
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from learnerbot.config import AppSettings
from learnerbot.transaction_audit import run_transaction_audit
from learnerbot.transaction_audit_worker_patch import _master_chat_ids, send_audit_document


def main():
    parser = argparse.ArgumentParser(description="Export public on-chain transactions and bot execution data for every registered Telegram ID/wallet.")
    parser.add_argument("--hours", type=float, default=2.0, help="Lookback window before the built-in 15-minute overlap (default: 2 hours)")
    parser.add_argument("--send-telegram", action="store_true", help="Send resulting ZIP to active MASTER Telegram account(s)")
    args = parser.parse_args()

    app = AppSettings.load()
    result = run_transaction_audit(app, hours=max(0.25, args.hours))
    if args.send_telegram:
        for tid in _master_chat_ids(app):
            send_audit_document(app, tid, result["latest_zip"], result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
