from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from learnerbot.strategy_ai_proposals import validate_ai_strategy_proposal
from learnerbot.strategy_lab import connect, register_strategy
from learnerbot.strategy_lab_research import (
    PUBLIC_RESEARCH_TOOLS,
    asset_request_report,
    ensure_cross_chain_scope,
    profitable_wallet_research,
    request_asset,
)


def _app(tmp_path):
    csv_dir = tmp_path / "csv"
    data_dir = tmp_path / "data"
    csv_dir.mkdir()
    data_dir.mkdir()
    return SimpleNamespace(csv_dir=csv_dir, data_dir=data_dir)


def test_profitable_wallet_research_is_anonymised_and_money_weighted(tmp_path):
    app = _app(tmp_path)
    db = app.data_dir / "base.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE profit_evidence(
          tx_hash TEXT, wallet TEXT, net_base REAL, proof_quality TEXT, route_fingerprint TEXT
        );
        CREATE TABLE strategy_patterns(
          pattern_id TEXT, strategy_class TEXT, tx_count INTEGER, wallet_count INTEGER,
          proven_profit_count INTEGER, avg_net_base REAL, confidence REAL,
          replicability REAL, status TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO profit_evidence VALUES(?,?,?,?,?)",
        [
            ("a", "0x1111111111111111111111111111111111111111", 1.0, "PROVEN_WRAPPED_BASE", "r1"),
            ("b", "0x1111111111111111111111111111111111111111", -0.2, "PROVEN_WRAPPED_BASE", "r2"),
            ("c", "0x1111111111111111111111111111111111111111", 0.5, "PROVEN_WRAPPED_BASE", "r1"),
        ],
    )
    conn.execute(
        "INSERT INTO strategy_patterns VALUES(?,?,?,?,?,?,?,?,?)",
        ("p1", "ARBITRAGE", 10, 3, 5, 0.03, 80, 75, "PROVEN"),
    )
    conn.commit()
    conn.close()

    report = profitable_wallet_research(app)
    wallet = report["chains"][0]["profitable_wallet_cohorts"][0]
    assert wallet["wallet_ref"].startswith("wallet_")
    assert "0x1111" not in json.dumps(report)
    assert wallet["net_base"] == 1.3
    assert wallet["gross_negative_base"] == 0.2
    assert wallet["profit_factor"] > 1
    assert report["chains"][0]["learned_patterns"][0]["strategy_class"] == "ARBITRAGE"


def test_asset_request_is_review_only_and_deduplicated(tmp_path):
    app = _app(tmp_path)
    first = request_asset(
        app,
        chain="EVM",
        asset="0x2222222222222222222222222222222222222222",
        symbol="TST",
        reason="shadow strategy found repeated executable edge",
        evidence="pool/liquidity evidence required",
        proposed_by="test",
    )
    second = request_asset(
        app,
        chain="EVM",
        asset="0x2222222222222222222222222222222222222222",
        symbol="TST",
        reason="updated research reason",
        proposed_by="test2",
    )
    report = asset_request_report(app)
    assert first["request_id"] == second["request_id"]
    assert len(report["pending"]) == 1
    assert report["pending"][0]["auto_added"] is False
    assert report["pending"][0]["status"] == "REQUESTED_REVIEW"


def test_cross_chain_activation_marks_lab_metadata_and_seeds_predictive_research(tmp_path):
    app = _app(tmp_path)
    existing = register_strategy(
        app,
        name="Test Momentum",
        family="MOMENTUM",
        source="MARKET_NATIVE",
        hypothesis="test hypothesis",
        params={"x": 1},
    )
    result = ensure_cross_chain_scope(app)
    with connect(app) as conn:
        row = conn.execute(
            "SELECT params_json FROM strategy_lab_registry WHERE strategy_id=?",
            (existing["strategy_id"],),
        ).fetchone()
        params = json.loads(row["params_json"])
        names = {r[0] for r in conn.execute("SELECT name FROM strategy_lab_registry").fetchall()}
    assert params["chain_scope"] == ["SOLANA", "EVM"]
    assert params["chain_specific_cost_model_required"] is True
    assert "Profitable Wallet Pattern Transfer" in names
    assert "Forecasted Positive Net Edge" in names
    assert result["research_strategies_seeded"]


def test_ai_proposal_accepts_cross_chain_forecast_and_asset_request():
    proposal = {
        "name": "Forecasted Flow",
        "family": "PREDICTIVE",
        "hypothesis": "flow acceleration may forecast positive net edge",
        "market_regime": "liquid trending markets",
        "entry_logic": "enter only after forecast and common positive-edge gates agree",
        "exit_logic": "predeclared target/stop/time exit under common executor",
        "data_required": ["flow", "liquidity", "quotes", "costs"],
        "estimated_costs": "all fees, gas/priority, slippage and price impact",
        "failure_modes": ["lookahead", "regime shift"],
        "shadow_test": "time-ordered shadow replay and forward shadow",
        "minimum_observation_windows": 6,
        "minimum_trades": 30,
        "falsification_conditions": ["negative net after costs", "poor calibration"],
        "differentiation": "does not depend on copying one wallet",
        "chain_scope": ["SOLANA", "EVM"],
        "research_plan": ["compare profitable wallet cohorts", "research public DEX data"],
        "research_tools": ["Dune", "DEX Screener API", "GitHub public code search"],
        "forecast": {
            "target": "positive_net_edge_after_costs",
            "horizon": "5 minutes",
            "features": ["flow_acceleration", "liquidity_change"],
            "model_family": "calibrated classifier",
            "trade_threshold": "probability >= calibrated threshold",
            "calibration_metric": "Brier score",
            "validation_split": "strict time ordered",
            "abstain_rule": "no trade when uncertainty is high",
            "expected_edge_output": "expected net edge",
        },
        "asset_requests": [
            {
                "chain": "SOLANA",
                "asset": "ExampleMintForResearchOnly",
                "symbol": "TST",
                "reason": "candidate absent from current universe",
                "evidence": "shadow evidence only",
            }
        ],
    }
    clean = validate_ai_strategy_proposal(proposal)
    assert clean["chain_scope"] == ["SOLANA", "EVM"]
    assert clean["forecast"]["target"] == "positive_net_edge_after_costs"
    assert clean["asset_requests"][0]["chain"] == "SOLANA"


def test_research_tool_catalogue_covers_wallet_market_code_and_cross_chain_sources():
    names = {row["tool"] for row in PUBLIC_RESEARCH_TOOLS}
    assert {"Dune", "DEX Screener API", "Etherscan API V2", "GitHub public code search", "DefiLlama", "Jupiter"} <= names
    assert all(row["safe_mode"].startswith(("READ_ONLY", "QUOTE")) for row in PUBLIC_RESEARCH_TOOLS)
