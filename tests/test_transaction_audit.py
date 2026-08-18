from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

from learnerbot import transaction_audit as audit
from learnerbot import transaction_audit_worker_patch as worker


def _app(tmp_path):
    return SimpleNamespace(
        data_dir=tmp_path / "data",
        csv_dir=tmp_path / "CSVbot",
        etherscan_api_key="",
        telegram_bot_token="",
        telegram_chat_ids=[],
    )


def _row():
    return {
        "telegram_id": "123",
        "user_role": "USER",
        "user_status": "ACTIVE",
        "wallet_type": "SOLANA",
        "wallet_id": "s1",
        "wallet_label": "Test",
        "wallet_address": "WalletAddress",
        "chain_slug": "solana",
        "chain_id": "solana",
        "source": "solana_rpc",
        "tx_hash": "sig1",
        "time_epoch": 1000,
        "time_utc": "1970-01-01 00:16:40 UTC",
        "block_number": "10",
        "status": "SUCCESS",
        "direction": "OUT",
        "action": "BUY",
        "asset": "Mint",
        "token_address": "Mint",
        "amount": "1",
        "amount_raw": "100",
        "native_delta": "-0.001",
        "fee_native": "0.000005",
        "from_address": "WalletAddress",
        "to_address": "",
        "method": "Jupiter",
        "details_json": "{}",
        "explorer_url": "https://solscan.io/tx/sig1",
    }


def test_audit_interval_is_exactly_two_hours():
    assert audit.AUDIT_INTERVAL_SECONDS == 7200


def test_direction_classification():
    addr = "0xAbC"
    assert audit._evm_direction(addr, "0xabc", "0xdef") == "OUT"
    assert audit._evm_direction(addr, "0xdef", "0xABC") == "IN"
    assert audit._evm_direction(addr, "0xabc", "0xABC") == "SELF"


def test_direct_export_script_resolves_repo_package():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "export_all_user_transactions.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "--send-telegram" in result.stdout


def test_run_transaction_audit_builds_zip_and_cumulative(monkeypatch, tmp_path):
    app = _app(tmp_path)
    app.data_dir.mkdir(parents=True)
    app.csv_dir.mkdir(parents=True)
    wallet = {
        "telegram_id": "123", "user_role": "USER", "user_status": "ACTIVE",
        "wallet_type": "SOLANA", "wallet_id": "s1", "wallet_label": "Test",
        "wallet_address": "WalletAddress", "active": True,
    }
    monkeypatch.setattr(audit, "_wallet_inventory", lambda app: ([{"telegram_id": "123"}], [wallet]))
    monkeypatch.setattr(audit, "collect_solana", lambda app, wallets, since: ([_row()], []))
    monkeypatch.setattr(audit, "collect_evm", lambda app, wallets, since: ([], []))
    monkeypatch.setattr(audit, "_export_db_tables", lambda app, run_dir, since: [])
    monkeypatch.setattr(audit, "_copy_strategy_snapshot", lambda app, run_dir: [])

    result = audit.run_transaction_audit(app, hours=2)
    latest = Path(result["latest_zip"])
    assert latest.exists()
    assert result["registered_users"] == 1
    assert result["enabled_wallets"] == 1
    assert result["solana_transactions"] == 1
    assert result["cumulative_rows"] == 1

    with zipfile.ZipFile(latest) as zf:
        names = set(zf.namelist())
        assert "all_transactions.csv" in names
        assert "solana_transactions.csv" in names
        assert "evm_transactions.csv" in names
        assert "wallet_inventory.csv" in names
        assert "summary.json" in names
        summary = json.loads(zf.read("summary.json"))
        assert "private keys" in summary["privacy"].lower()


def test_master_delivery_targets_only_active_masters(monkeypatch, tmp_path):
    app = _app(tmp_path)
    monkeypatch.setattr(worker, "all_users", lambda csv_dir, enabled_only=False: [
        {"telegram_id": "1", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "2", "role": "USER", "status": "ACTIVE"},
        {"telegram_id": "3", "role": "MASTER", "status": "SUSPENDED"},
    ])
    assert worker._master_chat_ids(app) == ["1"]
