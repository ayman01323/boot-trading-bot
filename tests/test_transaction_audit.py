from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from learnerbot import hourly_gpt_strategy_review as gpt_review
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


def test_worker_interval_is_exactly_one_hour():
    assert worker.HOURLY_INTERVAL_SECONDS == 3600


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


def test_direct_export_script_prefers_production_venv():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "export_all_user_transactions.py").read_text(encoding="utf-8")
    assert '".venv" / "bin" / "python"' in script
    assert "BOOT_AUDIT_VENV_REEXEC" in script
    assert "os.execve" in script


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

    result = audit.run_transaction_audit(app, hours=1)
    latest = Path(result["latest_zip"])
    assert latest.exists()
    assert result["requested_hours"] == 1.0
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
        assert "cumulative_all_transactions.csv" in names
        summary = json.loads(zf.read("summary.json"))
        assert "private keys" in summary["privacy"].lower()


def _make_review_zip(tmp_path: Path) -> Path:
    path = tmp_path / "audit.zip"
    headers = list(_row().keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    writer.writerow(_row())
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("all_transactions.csv", buf.getvalue())
        zf.writestr("collection_errors.csv", "telegram_id,wallet,chain,stage,error\n")
        zf.writestr("summary.json", json.dumps({
            "requested_hours": 1.0,
            "window_start_utc": "2026-08-18 20:00:00 UTC",
            "registered_users": 1,
            "enabled_wallets": 1,
            "collection_errors": 0,
            "cumulative_rows": 1,
        }))
    return path


def test_gpt_metrics_anonymise_ids_and_omit_wallet_address(tmp_path):
    metrics = gpt_review.build_review_metrics(_make_review_zip(tmp_path))
    encoded = json.dumps(metrics)
    assert "WalletAddress" not in encoded
    assert '"123"' not in encoded
    assert "user_" in encoded


def test_gpt_review_requires_shadow_only_human_approval():
    valid = {
        "do_not_auto_deploy_live": True,
        "shadow_candidate": {
            "mode": "SHADOW_ONLY",
            "live_promotion_requires_human_approval": True,
        },
    }
    gpt_review._validate_review(valid)
    invalid = {
        "do_not_auto_deploy_live": False,
        "shadow_candidate": {
            "mode": "LIVE",
            "live_promotion_requires_human_approval": False,
        },
    }
    with pytest.raises(RuntimeError):
        gpt_review._validate_review(invalid)


def test_gpt_request_uses_store_false_and_structured_schema(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            review = {
                "status": "WATCH",
                "executive_summary": "Operational review only.",
                "findings": [],
                "shadow_candidate": {
                    "mode": "SHADOW_ONLY",
                    "hypothesis": "Observe execution reliability.",
                    "experiments": [],
                    "live_promotion_requires_human_approval": True,
                },
                "recommended_action": "RUN_SHADOW_EXPERIMENTS",
                "do_not_auto_deploy_live": True,
            }
            return {
                "id": "resp_test",
                "model": "gpt-test",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(review)}]}],
            }

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "body": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr(gpt_review.requests, "post", fake_post)
    review, meta = gpt_review.request_gpt_review({"transaction_rows": 1}, api_key="test-key", model="gpt-test")
    assert captured["body"]["store"] is False
    assert captured["body"]["text"]["format"]["type"] == "json_schema"
    assert captured["body"]["text"]["format"]["strict"] is True
    assert review["shadow_candidate"]["mode"] == "SHADOW_ONLY"
    assert review["do_not_auto_deploy_live"] is True
    assert meta["response_id"] == "resp_test"


def test_master_delivery_targets_only_active_masters(monkeypatch, tmp_path):
    app = _app(tmp_path)
    monkeypatch.setattr(worker, "all_users", lambda csv_dir, enabled_only=False: [
        {"telegram_id": "1", "role": "MASTER", "status": "ACTIVE"},
        {"telegram_id": "2", "role": "USER", "status": "ACTIVE"},
        {"telegram_id": "3", "role": "MASTER", "status": "SUSPENDED"},
    ])
    assert worker._master_chat_ids(app) == ["1"]
