from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

from learnerbot import loss_forensics_github_export as forensic


def _app(tmp_path):
    root = tmp_path / "repo"
    data = root / "data"
    csv_dir = root / "CSVbot"
    data.mkdir(parents=True)
    csv_dir.mkdir(parents=True)
    return SimpleNamespace(root=root, data_dir=data, csv_dir=csv_dir)


def _audit_zip(tmp_path):
    path = tmp_path / "latest_all_ids.zip"
    headers = [
        "telegram_id", "wallet_address", "chain_slug", "action", "status",
        "fee_native", "native_delta", "source", "tx_hash",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    writer.writerow({
        "telegram_id": "PRIVATE_TID", "wallet_address": "PRIVATE_WALLET",
        "chain_slug": "solana", "action": "SELL", "status": "SUCCESS",
        "fee_native": "0.000005", "native_delta": "0.01",
        "source": "solana_rpc", "tx_hash": "public_signature",
    })
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("all_transactions.csv", buf.getvalue())
        zf.writestr("collection_errors.csv", "telegram_id,wallet,chain,stage,error\n")
        zf.writestr("summary.json", json.dumps({
            "requested_hours": 1.0,
            "window_start_utc": "2026-08-19 00:00:00 UTC",
            "registered_users": 1,
            "enabled_wallets": 1,
            "collection_errors": 0,
            "cumulative_rows": 1,
        }))
    return path


def _seed_solana(app):
    path = Path(app.data_dir) / "solana_sibot.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE positions(
      position_id TEXT,mint TEXT,mode TEXT,status TEXT,token_amount_raw TEXT,
      entry_cost_sol TEXT,entry_ts INTEGER,current_exit_sol TEXT,
      unrealised_net_sol TEXT,unrealised_pct REAL,peak_unrealised_pct REAL,
      realised_net_sol TEXT,exit_signature TEXT,exit_reason TEXT,closed_at INTEGER,
      updated_at INTEGER
    );
    CREATE TABLE live_exit_circuit(
      position_id TEXT,status TEXT,tx_signature TEXT,error TEXT,fraction TEXT,
      close_reason TEXT,sell_raw TEXT,opened_at INTEGER,updated_at INTEGER,
      telegram_id TEXT,payload_json TEXT
    );
    """)
    now = 2_000_000_000
    conn.executemany(
        "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("p_win", "MintWin", "LIVE", "CLOSED", "0", "0.10", now-100,
             "0", "0", 0, 0, "0.030", "sig_win", "LEADER_EXIT", now-50, now-50),
            ("p_loss", "MintLoss", "LIVE", "CLOSED", "0", "0.10", now-100,
             "0", "0", 0, 0, "-0.020", "sig_loss", "STOP_LOSS", now-40, now-40),
            ("p_open", "MintOpen", "LIVE", "OPEN", "100", "0.05", now-20,
             "0.04", "-0.01", -20.0, 3.0, "0", "", "", None, now-10),
        ],
    )
    conn.execute(
        "INSERT INTO live_exit_circuit VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("p_open", "RECONCILING", "sig_pending", "rpc lag", "1", "LEADER_EXIT",
         "100", now-9, now-8, "PRIVATE_TID", '{"wallet":"PRIVATE_WALLET"}'),
    )
    conn.commit()
    conn.close()
    return now


def _seed_control(app, now):
    path = Path(app.data_dir) / "profit_control_loop.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE control_state(key TEXT,value TEXT);
    CREATE TABLE control_runs(
      generated_at INTEGER,profile TEXT,closed_trades INTEGER,wins INTEGER,losses INTEGER,
      gross_profit_sol TEXT,gross_loss_sol TEXT,net_sol TEXT,profit_factor TEXT,
      profile_changed INTEGER,previous_profile TEXT,gpt_status TEXT,details_json TEXT
    );
    CREATE TABLE strategy_registry(
      profile TEXT,hours_observed INTEGER,closed_trades INTEGER,wins INTEGER,losses INTEGER,
      gross_profit_sol TEXT,gross_loss_sol TEXT,net_sol TEXT,profit_factor TEXT,
      successful INTEGER,last_used_at INTEGER,updated_at INTEGER
    );
    """)
    conn.execute("INSERT INTO control_state VALUES('active_profile','PROFIT_FIRST')")
    conn.execute(
        "INSERT INTO control_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (now-5, "PROFIT_FIRST", 2, 1, 1, "0.03", "0.02", "0.01", "1.5", 0, "BASELINE", "WATCH", '{"telegram_id":"PRIVATE_TID"}'),
    )
    conn.execute(
        "INSERT INTO strategy_registry VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("PROFIT_FIRST", 3, 8, 5, 3, "0.12", "0.08", "0.04", "1.5", 1, now-5, now-5),
    )
    conn.commit()
    conn.close()


def test_build_forensics_is_amount_based_and_sanitized(monkeypatch, tmp_path):
    app = _app(tmp_path)
    now = _seed_solana(app)
    _seed_control(app, now)
    monkeypatch.setattr(forensic.time, "time", lambda: now)

    report = forensic.build_loss_forensics(app, _audit_zip(tmp_path), hours=12)
    perf = report["solana_live"]["performance"]
    assert perf["gross_profit_sol"] == "0.030"
    assert perf["gross_loss_sol"] == "0.020"
    assert perf["net_sol"] == "0.010"
    assert perf["profit_factor"] == "1.5"
    assert perf["profit_amount_exceeds_loss_amount"] is True
    assert report["profit_control"]["state"]["active_profile"] == "PROFIT_FIRST"
    assert report["solana_live"]["exit_circuit_status_counts"]["RECONCILING"] == 1

    encoded = json.dumps(report)
    assert "PRIVATE_TID" not in encoded
    assert "PRIVATE_WALLET" not in encoded
    assert "details_json" not in encoded
    assert "payload_json" not in encoded
    assert "telegram_id" not in encoded


def test_publish_failure_is_best_effort(monkeypatch, tmp_path):
    app = _app(tmp_path)
    monkeypatch.setattr(forensic, "build_loss_forensics", lambda *args, **kwargs: {"safe": True})
    monkeypatch.setattr(forensic, "_publish_git", lambda *args, **kwargs: {"ok": False, "error": "no credentials"})
    result = forensic.publish_loss_forensics(app, tmp_path / "missing.zip")
    assert result["ok"] is False
    assert result["error"] == "no credentials"
    assert result["report"] == {"safe": True}
