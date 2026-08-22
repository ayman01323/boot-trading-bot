from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "deepseek_trade_lookup", ROOT / "scripts" / "deepseek_trade_lookup.py"
)
assert SPEC and SPEC.loader
trade_lookup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trade_lookup)


def _configure_runtime(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    csv_dir = root / "CSVbot"
    data_dir = root / "data"
    (csv_dir / "auto").mkdir(parents=True)
    data_dir.mkdir(parents=True)
    trade_lookup.RUNTIME_ROOT = root
    trade_lookup.CSV_DIR = csv_dir
    trade_lookup.DATA_DIR = data_dir
    trade_lookup.PROVENANCE_DB = csv_dir / "auto" / "trade_provenance.sqlite3"
    trade_lookup.AUTO_EXECUTION_CSV = csv_dir / "auto" / "auto_trade_execution.csv"
    trade_lookup.AUTO_SIMULATION_CSV = csv_dir / "auto" / "auto_trade_simulations.csv"
    trade_lookup.EVM_SIBOT_DB = data_dir / "sibot.sqlite3"
    trade_lookup.SOLANA_SIBOT_DB = data_dir / "solana_sibot.sqlite3"


def _make_provenance(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE trade_events(
        event_id TEXT PRIMARY KEY,event_ts INTEGER,telegram_id TEXT,wallet_id TEXT,
        chain_id TEXT,chain_slug TEXT,strategy_engine TEXT,strategy_version TEXT,
        git_sha TEXT,action TEXT,tx_hash TEXT,status TEXT,realised_pnl TEXT,
        profit_fee TEXT,metadata_json TEXT,created_at INTEGER)"""
    )
    conn.execute(
        "INSERT INTO trade_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "event123", 200, "5923828381", "w1", "-101", "solana",
            "SIBOT_SOLANA_COPY", "v1", "a" * 40, "SELL", "sig123",
            "SUCCESS", "0.01", "0", json.dumps({"mint": "Mint1", "secret": "drop-me"}), 200,
        ),
    )
    conn.commit()
    conn.close()


def _make_solana(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE positions(
        position_id TEXT PRIMARY KEY,telegram_id TEXT,mint TEXT,mode TEXT,status TEXT,
        token_amount_raw TEXT,entry_cost_sol TEXT,entry_ts INTEGER,leader_buy_signature TEXT,
        current_exit_sol TEXT,unrealised_net_sol TEXT,unrealised_pct REAL,
        peak_unrealised_pct REAL,realised_net_sol TEXT,exit_signature TEXT,exit_reason TEXT,
        closed_at INTEGER,updated_at INTEGER,strategy_engine TEXT,strategy_version TEXT,git_sha TEXT)"""
    )
    conn.execute(
        "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "posabc", "5923828381", "Mint1", "LIVE", "CLOSED", "1000", "0.5", 300,
            "buySig", "0.6", "0", 0.0, 10.0, "0.09", "sellSig", "TAKE_PROFIT",
            350, 350, "SIBOT_SOLANA_COPY", "v1", "b" * 40,
        ),
    )
    conn.execute(
        """CREATE TABLE live_execution_attempts(
        attempt_key TEXT PRIMARY KEY,telegram_id TEXT,mint TEXT,action TEXT,status TEXT,
        tx_signature TEXT,input_raw TEXT,output_raw TEXT,wallet_delta_lamports TEXT,
        error TEXT,created_at INTEGER,updated_at INTEGER)"""
    )
    conn.execute(
        "INSERT INTO live_execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("attempt1", "5923828381", "Mint1", "BUY", "EXECUTED", "buySig", "500", "1000", "-501", "", 290, 291),
    )
    conn.execute(
        """CREATE TABLE trades(
        trade_id TEXT PRIMARY KEY,mint TEXT,decimals INTEGER,buy_signature TEXT,sell_signature TEXT,
        buy_ts INTEGER,sell_ts INTEGER,token_amount_raw TEXT,cost_sol TEXT,proceeds_sol TEXT,
        net_sol TEXT,hold_seconds INTEGER,source TEXT,updated_at INTEGER)"""
    )
    conn.execute(
        "INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("leadertrade1", "Mint2", 9, "lb", "ls", 100, 150, "10", "1", "1.2", "0.2", 50, "history", 150),
    )
    conn.commit()
    conn.close()


def _make_evm(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE positions(
        position_id TEXT PRIMARY KEY,telegram_id TEXT,chain_id INTEGER,chain_slug TEXT,
        token TEXT,symbol TEXT,mode TEXT,status TEXT,token_amount_raw TEXT,
        entry_input_native TEXT,entry_cost_native TEXT,entry_tx TEXT,entry_ts INTEGER,
        current_exit_native TEXT,unrealised_net_native TEXT,unrealised_pct REAL,
        peak_unrealised_pct REAL,realised_net_native TEXT,realised_user_net_native TEXT,
        profit_fee_native TEXT,exit_tx TEXT,exit_reason TEXT,closed_at INTEGER,updated_at INTEGER,
        strategy_engine TEXT,strategy_version TEXT,git_sha TEXT)"""
    )
    conn.execute(
        "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "evmpos1", "5923828381", 8453, "base", "0xtoken", "TOK", "LIVE", "CLOSED", "100",
            "0.01", "0.011", "0xbuy", 250, "0.013", "0", 0.0, 20.0, "0.002", "0.002", "0",
            "0xsell", "LEADER_EXIT", 280, 280, "SIBOT_EVM_COPY", "v2", "c" * 40,
        ),
    )
    conn.execute(
        """CREATE TABLE wallet_trades(
        trade_id TEXT PRIMARY KEY,chain_id INTEGER,chain_slug TEXT,token TEXT,symbol TEXT,
        buy_tx TEXT,sell_tx TEXT,buy_ts INTEGER,sell_ts INTEGER,token_amount_raw TEXT,
        cost_native TEXT,proceeds_native TEXT,buy_gas_native TEXT,sell_gas_native TEXT,
        net_native TEXT,source TEXT,updated_at INTEGER)"""
    )
    conn.execute(
        "INSERT INTO wallet_trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("hist1", 8453, "base", "0xtoken", "TOK", "0xb", "0xs", 10, 20, "1", "1", "2", "0.01", "0.01", "0.98", "history", 20),
    )
    conn.commit()
    conn.close()


def _make_csvs(csv_dir: Path) -> None:
    execution = csv_dir / "auto" / "auto_trade_execution.csv"
    with execution.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "timestamp_epoch", "telegram_id", "wallet_id", "chain_id", "chain_slug",
                "strategy_engine", "strategy_version", "git_sha", "route_id", "route_path",
                "input_base", "expected_gross_base", "expected_gas_base", "expected_net_base",
                "realised_net_base", "profit_fee_base", "fee_tx_hash", "tx_hash", "status", "note",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "timestamp_epoch": "400", "telegram_id": "5923828381", "wallet_id": "w1",
            "chain_id": "8453", "chain_slug": "base", "strategy_engine": "AUTO_EVM_ARBITRAGE",
            "strategy_version": "v3", "git_sha": "d" * 40, "route_id": "r1", "route_path": "A>B>A",
            "input_base": "0.01", "expected_gross_base": "0.002", "expected_gas_base": "0.0001",
            "expected_net_base": "0.0019", "realised_net_base": "0.0018", "profit_fee_base": "0",
            "fee_tx_hash": "", "tx_hash": "0xtrade", "status": "SUCCESS", "note": "ok",
        })
    simulation = csv_dir / "auto" / "auto_trade_simulations.csv"
    with simulation.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "timestamp_epoch", "telegram_id", "wallet_id", "chain_id", "chain_slug",
                "strategy_engine", "strategy_version", "git_sha", "route_id", "route_path",
                "input_base", "min_net_profit_base", "gross_profit_base", "gas_cost_base",
                "simulation_ok", "reason",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "timestamp_epoch": "390", "telegram_id": "5923828381", "wallet_id": "w1",
            "chain_id": "8453", "chain_slug": "base", "strategy_engine": "AUTO_EVM_ARBITRAGE",
            "strategy_version": "v3", "git_sha": "d" * 40, "route_id": "r1", "route_path": "A>B>A",
            "input_base": "0.01", "min_net_profit_base": "0.0002", "gross_profit_base": "0.002",
            "gas_cost_base": "0.0001", "simulation_ok": "true", "reason": "",
        })


def test_identifier_validation_rejects_injection() -> None:
    assert trade_lookup.validate_account_id("5923828381")
    assert not trade_lookup.validate_account_id("5923828381;rm")
    assert trade_lookup.validate_exact_id("abc-123_def:0x00")
    for bad in ("../etc/passwd", "'; DROP TABLE positions; --", "$(id)", "a/b"):
        assert not trade_lookup.validate_exact_id(bad)


def test_account_lookup_uses_real_sources_and_named_fields(tmp_path: Path) -> None:
    _configure_runtime(tmp_path)
    _make_provenance(trade_lookup.PROVENANCE_DB)
    _make_solana(trade_lookup.SOLANA_SIBOT_DB)
    _make_evm(trade_lookup.EVM_SIBOT_DB)
    _make_csvs(trade_lookup.CSV_DIR)

    result = trade_lookup.account_lookup("5923828381")
    assert result["count"] >= 6
    assert result["records"][0]["source"] == "auto_trade_execution"
    assert any(r.get("record_id") == "posabc" for r in result["records"])
    assert any(r.get("record_id") == "evmpos1" for r in result["records"])
    provenance = next(r for r in result["records"] if r.get("record_id") == "event123")
    assert provenance["strategy_version"] == "v1"
    assert provenance["metadata"] == {"mint": "Mint1"}
    assert "secret" not in json.dumps(result).lower()


def test_exact_lookup_supports_position_event_and_historical_trade(tmp_path: Path) -> None:
    _configure_runtime(tmp_path)
    _make_provenance(trade_lookup.PROVENANCE_DB)
    _make_solana(trade_lookup.SOLANA_SIBOT_DB)
    _make_evm(trade_lookup.EVM_SIBOT_DB)

    assert any(r["record_id"] == "posabc" for r in trade_lookup.exact_lookup("posabc")["records"])
    assert any(r["record_id"] == "event123" for r in trade_lookup.exact_lookup("event123")["records"])
    assert any(r["record_id"] == "hist1" for r in trade_lookup.exact_lookup("hist1")["records"])
    assert any(r["record_id"] == "leadertrade1" for r in trade_lookup.exact_lookup("leadertrade1")["records"])


def test_not_found_is_bounded(tmp_path: Path) -> None:
    _configure_runtime(tmp_path)
    result = trade_lookup.account_lookup("5923828381")
    assert result["not_found"] is True
    assert result["records"] == []
