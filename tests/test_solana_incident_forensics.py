from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from learnerbot import solana_incident_forensics_patch as mod


def _db(path: Path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE positions(
          position_id TEXT PRIMARY KEY, telegram_id TEXT, leader_wallet TEXT, leader_rank INTEGER,
          mint TEXT, mode TEXT, status TEXT, token_amount_raw TEXT, entry_cost_sol TEXT,
          entry_ts INTEGER, current_exit_sol TEXT, unrealised_net_sol TEXT, unrealised_pct REAL,
          peak_unrealised_pct REAL, realised_net_sol TEXT, exit_reason TEXT, closed_at INTEGER,
          updated_at INTEGER, leader_buy_signature TEXT, exit_signature TEXT,
          strategy_engine TEXT, strategy_version TEXT, git_sha TEXT
        );
        CREATE TABLE live_execution_attempts(
          attempt_key TEXT PRIMARY KEY, telegram_id TEXT, leader_wallet TEXT, leader_signature TEXT,
          mint TEXT, action TEXT, status TEXT, tx_signature TEXT, input_raw TEXT, output_raw TEXT,
          wallet_delta_lamports TEXT, error TEXT, created_at INTEGER, updated_at INTEGER
        );
        CREATE TABLE live_exit_circuit(
          position_id TEXT, status TEXT, tx_signature TEXT, error TEXT, fraction TEXT,
          close_reason TEXT, sell_raw TEXT, opened_at INTEGER, updated_at INTEGER
        );
        CREATE TABLE live_decisions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, telegram_id TEXT, leader_wallet TEXT,
          signature TEXT, event_action TEXT, mint TEXT, decision TEXT, reason TEXT
        );
        CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT);
        """
    )
    return conn


def test_incident_report_reconstructs_closed_entry_cost_and_redacts_identity(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "solana_sibot.sqlite3"
    conn = _db(db)
    entry = mod._BOUNDARIES[1][1] + 60
    close = entry + 120
    conn.execute(
        """INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "position-123456789", "5923828381", "LeaderWalletABC", 1,
            "MintAddressLongEnough123456789", "LIVE", "CLOSED", "0", "0",
            entry, "0", "0", 0.0, 8.0, "-0.0002", "STOP_LOSS", close,
            close, "leader-buy-sig", "exit-sig", "SIBOT_SOLANA_COPY", "v2.3", "abc123sha",
        ),
    )
    conn.execute(
        """INSERT INTO live_execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "attempt", "5923828381", "LeaderWalletABC", "leader-buy-sig",
            "MintAddressLongEnough123456789", "BUY", "EXECUTED", "buy-tx",
            "500000", "1", "-500000", "", entry, entry,
        ),
    )
    conn.execute(
        "INSERT INTO live_decisions(ts,telegram_id,leader_wallet,signature,event_action,mint,decision,reason) VALUES(?,?,?,?,?,?,?,?)",
        (entry, "5923828381", "LeaderWalletABC", "leader-buy-sig", "BUY", "MintAddressLongEnough123456789", "BUY", "accepted"),
    )
    conn.commit()
    conn.close()

    report = mod._incident_report(SimpleNamespace(data_dir=data))
    assert report["available"] is True
    assert report["all_live_positions"]["closed"] == 1
    assert report["all_live_positions"]["losses"] == 1
    assert report["all_live_positions"]["net_sol"] == "-0.0002"
    row = report["positions"][0]
    assert row["entry_cash_sol"] == "0.0005"
    assert round(row["realised_pct"], 2) == -40.0
    assert "STOP_LOSS_TRIGGERED" in row["loss_flags"]
    assert "GAVE_BACK_PRIOR_PROFIT" in row["loss_flags"]
    assert row["git_sha"] == "abc123sha"
    assert row["leader_id"].startswith("leader-")
    assert "LeaderWalletABC" not in str(report)
    assert "5923828381" not in str(report)
    assert report["timeline_windows"]["aug18_relaxed_to_aug21_quality_restore"]["pnl"]["entries"] == 1


def test_summary_distinguishes_open_positions():
    summary = mod._summary([
        {"status": "CLOSED", "realised_net_sol": "0.1"},
        {"status": "CLOSED", "realised_net_sol": "-0.04"},
        {"status": "OPEN", "realised_net_sol": "0"},
    ])
    assert summary["entries"] == 3
    assert summary["closed"] == 2
    assert summary["open"] == 1
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["net_sol"] == "0.06"
    assert summary["profit_factor"] == "2.5"
