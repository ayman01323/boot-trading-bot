from __future__ import annotations

import csv
import sqlite3
import time
from pathlib import Path

from learnerbot.cross_chain_strategy_signals import evaluate_all
from learnerbot.market_feature_adapter import adapt_evm_opportunity, load_solana_market_features
from learnerbot.shadow_strategy_executor import connect as shadow_connect, run_shadow_cycle


class App:
    def __init__(self, root: Path):
        self.csv_dir = root / "CSVbot"
        self.data_dir = root / "data"
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


def _write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0]) if rows else ["empty"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)


def _evm_row(now: int) -> dict:
    return {
        "chain_slug": "base",
        "route_id": "route-1",
        "route_path": "0x0000000000000000000000000000000000000001>0x0000000000000000000000000000000000000002>0x0000000000000000000000000000000000000001",
        "observed_at_epoch": str(now),
        "source_input_base": "1",
        "expected_gross_profit_base": "0.010",
        "estimated_gas_base": "0.001",
        "builder_fee_base": "0.0005",
        "slippage_reserve_base": "0.0005",
        "price_impact_bps": "5",
        "liquidity_ok": "true",
        "sellability_ok": "true",
        "exact_quote_ok": "true",
        "simulation_ok": "true",
        "route_approved": "true",
        "whole_route_approved": "true",
        "atomic_profit_protection": "true",
    }


def test_evm_adapter_uses_measured_costs_and_exact_simulation(tmp_path):
    app = App(tmp_path)
    now = int(time.time())
    env = adapt_evm_opportunity(app, _evm_row(now), now=now)
    assert env.features.chain_type == "EVM"
    assert env.features.chain_slug == "base"
    assert env.features.gross_edge_bps == 100
    assert env.features.fees_bps == 15
    assert env.features.slippage_bps == 5
    assert env.features.price_impact_bps == 5
    assert env.features.net_edge_bps == 75
    assert env.features.liquidity_score == 1
    assert env.features.sellability_score == 1
    assert env.outcome_available is True
    assert env.outcome_basis == "EXACT_QUOTE_AND_SIMULATION"


def test_solana_adapter_does_not_invent_current_edge(tmp_path):
    app = App(tmp_path)
    now = int(time.time())
    db = app.data_dir / "solana_sibot.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE leader_events(
          event_id TEXT PRIMARY KEY, leader_wallet TEXT, signature TEXT, action TEXT, mint TEXT,
          token_amount_raw TEXT, sol_amount TEXT, event_ts INTEGER
        );
        CREATE TABLE trades(
          trade_id TEXT PRIMARY KEY, wallet TEXT, cost_sol TEXT, net_sol TEXT, sell_ts INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO leader_events VALUES(?,?,?,?,?,?,?,?)",
        ("e1", "wallet-a", "sig-a", "BUY", "mint-a", "1000", "1", now),
    )
    for i, net in enumerate(("0.10", "0.05", "-0.02", "0.03")):
        conn.execute(
            "INSERT INTO trades VALUES(?,?,?,?,?)",
            (f"t{i}", "wallet-a", "1", net, now - i),
        )
    conn.commit()
    conn.close()

    rows = load_solana_market_features(app, now=now)
    assert len(rows) == 1
    env = rows[0]
    assert env.features.chain_type == "SOLANA"
    assert env.features.gross_edge_bps == 0
    assert env.features.liquidity_score == 0
    assert env.features.sellability_score == 0
    assert env.features.forecast_positive_edge_probability > 0
    assert env.features.forecast_expected_net_bps == 0
    assert env.outcome_available is False
    assert not any(signal.eligible for signal in evaluate_all(env.features))


def test_shadow_cycle_persists_quote_scorecard_without_promotion_evidence(tmp_path):
    app = App(tmp_path)
    now = int(time.time())
    _write_csv(app.csv_dir / "live_opportunities.csv", [_evm_row(now)])

    report = run_shadow_cycle(app, now=now)
    assert report["safety"]["signing"] is False
    assert report["safety"]["transaction_submission"] is False
    assert report["safety"]["live_auto_promotion"] is False
    assert report["evaluation"]["promotion_evidence_created"] == 0

    arb = report["scorecard"]["strategy_scorecards"]["Cross Venue Net Arbitrage"]
    assert arb["opportunities"] == 1
    assert arb["eligible_signals"] == 1
    assert arb["executable_quote_simulations"] == 1
    assert arb["promotion_evidence"] == 0
    assert arb["promotion_allowed_from_this_scorecard"] is False
    assert float(arb["quote_simulated_net_base"]) > 0

    with shadow_connect(app) as conn:
        rows = conn.execute(
            "SELECT strategy,eligible,outcome_available,promotion_evidence FROM shadow_strategy_events"
        ).fetchall()
    assert len(rows) == 7
    assert all(int(row["promotion_evidence"]) == 0 for row in rows)

    # Running the same market observation again is idempotent.
    again = run_shadow_cycle(app, now=now)
    assert again["evaluation"]["inserted"] == 0
    assert again["evaluation"]["duplicates_ignored"] == 7
