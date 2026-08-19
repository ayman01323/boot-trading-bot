from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import time
import warnings
import zipfile
from collections import defaultdict
from decimal import Decimal
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


def test_temporary_public_solana_loss_forensics():
    """Temporary CI-only chain probe anchored on the reported failed-exit signature.

    This test never signs or broadcasts. It reads public finalized/confirmed chain
    data and emits a compact warning so the investigation result is recoverable
    from GitHub Actions logs. It lives only on the investigation branch.
    """
    anchor = "2eQUeOzkKUVXpzEMV2QXcTR45gV6CVN9qif3aP06WGRBq5MUCYpWHeY3V78gMkj9TxueMkb5sBHoD8mnfwtb3tcR"
    rpc_url = "https://api.mainnet-beta.solana.com"
    wsol = "So11111111111111111111111111111111111111112"

    def rpc(method, params):
        last = None
        for attempt in range(6):
            try:
                response = audit.requests.post(
                    rpc_url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=30,
                    headers={"User-Agent": "BOOT-loss-forensics/1.0"},
                )
                if response.status_code == 429:
                    time.sleep(1.0 + attempt * 1.25)
                    continue
                response.raise_for_status()
                payload = response.json()
                if payload.get("error"):
                    last = RuntimeError(str(payload["error"]))
                    time.sleep(0.5 + attempt * 0.5)
                    continue
                return payload.get("result")
            except Exception as exc:
                last = exc
                time.sleep(0.5 + attempt * 0.75)
        raise RuntimeError(f"RPC {method} failed: {last}")

    def keys(tx):
        raw = (((tx or {}).get("transaction") or {}).get("message") or {}).get("accountKeys") or []
        return [str(x.get("pubkey") or "") if isinstance(x, dict) else str(x) for x in raw]

    def owner_state(tx, wallet):
        meta = (tx or {}).get("meta") or {}
        pre = defaultdict(int); post = defaultdict(int); decimals = {}
        for field, target in (("preTokenBalances", pre), ("postTokenBalances", post)):
            for row in meta.get(field) or []:
                if str(row.get("owner") or "") != wallet:
                    continue
                mint = str(row.get("mint") or "")
                ui = row.get("uiTokenAmount") or {}
                target[mint] += int(ui.get("amount") or 0)
                decimals[mint] = int(ui.get("decimals") or 0)
        return dict(pre), dict(post), decimals

    def sol_delta(tx, wallet):
        ak = keys(tx)
        try:
            idx = ak.index(wallet)
        except ValueError:
            return Decimal(0)
        meta = (tx or {}).get("meta") or {}
        pre = meta.get("preBalances") or []; post = meta.get("postBalances") or []
        if idx >= len(pre) or idx >= len(post):
            return Decimal(0)
        return Decimal(int(post[idx]) - int(pre[idx])) / Decimal(1_000_000_000)

    anchor_tx = rpc("getTransaction", [anchor, {"commitment": "confirmed", "maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
    if not anchor_tx:
        warnings.warn("FORENSICS_JSON=" + json.dumps({"error": "anchor_not_visible"}, separators=(",", ":")))
        return

    raw_keys = (((anchor_tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or [])
    signers = [str(x.get("pubkey") or "") for x in raw_keys if isinstance(x, dict) and x.get("signer")]
    wallet = signers[0] if signers else keys(anchor_tx)[0]
    anchor_pre, anchor_post, _ = owner_state(anchor_tx, wallet)
    anchor_token_delta = {m: int(anchor_post.get(m, 0)) - int(anchor_pre.get(m, 0)) for m in set(anchor_pre) | set(anchor_post)}
    anchor_token_delta = {m: v for m, v in anchor_token_delta.items() if v and m != wsol}
    anchor_sol_delta = sol_delta(anchor_tx, wallet)
    anchor_ts = int(anchor_tx.get("blockTime") or 0)

    sig_rows = rpc("getSignaturesForAddress", [wallet, {"commitment": "confirmed", "limit": 250}]) or []
    start_ts = anchor_ts - 10 * 3600 if anchor_ts else 0
    end_ts = anchor_ts + 2 * 3600 if anchor_ts else 2**63 - 1
    selected = [r for r in sig_rows if start_ts <= int(r.get("blockTime") or 0) <= end_ts][:100]

    trades = []
    groups = defaultdict(lambda: {"buy": Decimal(0), "sell": Decimal(0), "fees": Decimal(0), "buys": 0, "sells": 0, "last_post": None, "txs": []})
    rpc_errors = []
    for idx, row in enumerate(reversed(selected)):
        sig = str(row.get("signature") or "")
        if not sig:
            continue
        try:
            tx = rpc("getTransaction", [sig, {"commitment": "confirmed", "maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
            if not tx:
                continue
            meta = tx.get("meta") or {}
            pre, post, decimals = owner_state(tx, wallet)
            deltas = {m: int(post.get(m, 0)) - int(pre.get(m, 0)) for m in set(pre) | set(post)}
            deltas.pop(wsol, None)
            positive = [(m, v) for m, v in deltas.items() if v > 0]
            negative = [(m, -v) for m, v in deltas.items() if v < 0]
            sd = sol_delta(tx, wallet)
            action = None; mint = None; raw = 0
            if meta.get("err") is None and sd < Decimal("-0.000005") and len(positive) == 1 and not negative:
                mint, raw = positive[0]; action = "BUY"
            elif meta.get("err") is None and sd > Decimal("0.000005") and len(negative) == 1 and not positive:
                mint, raw = negative[0]; action = "SELL"
            if action and mint:
                fee = Decimal(int(meta.get("fee") or 0)) / Decimal(1_000_000_000)
                g = groups[mint]
                if action == "BUY":
                    g["buy"] += -sd; g["buys"] += 1
                else:
                    g["sell"] += sd; g["sells"] += 1
                g["fees"] += fee; g["last_post"] = int(post.get(mint, 0)); g["txs"].append(sig)
                trades.append({
                    "ts": int(tx.get("blockTime") or 0), "sig": sig, "action": action,
                    "mint": mint, "token_raw": str(raw), "decimals": int(decimals.get(mint, 0)),
                    "sol_delta": str(sd), "fee_sol": str(fee), "post_raw": str(post.get(mint, 0)),
                })
        except Exception as exc:
            rpc_errors.append(f"{sig[:10]}:{type(exc).__name__}")
        if idx % 8 == 0:
            time.sleep(0.35)

    closed = []
    openish = []
    for mint, g in groups.items():
        net = g["sell"] - g["buy"]
        item = {
            "mint": mint, "buys": g["buys"], "sells": g["sells"],
            "buy_out_sol": str(g["buy"]), "sell_in_sol": str(g["sell"]),
            "net_sol": str(net), "fees_sol": str(g["fees"]), "last_post_raw": str(g["last_post"]),
            "txs": g["txs"],
        }
        if g["buys"] and g["sells"] and g["last_post"] == 0:
            closed.append(item)
        else:
            openish.append(item)
    closed.sort(key=lambda x: Decimal(x["net_sol"]))
    openish.sort(key=lambda x: Decimal(x["net_sol"]))
    gp = sum((Decimal(x["net_sol"]) for x in closed if Decimal(x["net_sol"]) > 0), Decimal(0))
    gl = sum((-Decimal(x["net_sol"]) for x in closed if Decimal(x["net_sol"]) < 0), Decimal(0))

    report = {
        "anchor": {
            "signature": anchor,
            "block_time": anchor_ts,
            "meta_err": (anchor_tx.get("meta") or {}).get("err"),
            "wallet": wallet,
            "sol_delta": str(anchor_sol_delta),
            "token_deltas_raw": anchor_token_delta,
            "input_token_decreased": any(v < 0 for v in anchor_token_delta.values()),
        },
        "window": {"start": start_ts, "end": end_ts, "signatures_seen": len(sig_rows), "transactions_examined": len(selected)},
        "closed_roundtrips": len(closed),
        "gross_profit_sol": str(gp), "gross_loss_sol": str(gl),
        "profit_factor": str(gp / gl) if gl > 0 else ("99" if gp > 0 else "0"),
        "net_closed_sol": str(gp - gl),
        "worst_closed": closed[:12],
        "open_or_partial": openish[:12],
        "classified_trades": trades[-40:],
        "rpc_errors": rpc_errors[:20],
    }
    warnings.warn("FORENSICS_JSON=" + json.dumps(report, separators=(",", ":"), default=str))
