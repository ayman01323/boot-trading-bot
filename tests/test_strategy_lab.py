from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from learnerbot import strategy_lab as lab


@dataclass
class App:
    data_dir: str


@pytest.fixture()
def app(tmp_path: Path) -> App:
    return App(str(tmp_path))


def _register(app, name="Test Strategy", source="AI_PROPOSED"):
    return lab.register_strategy(
        app,
        name=name,
        family="TEST",
        source=source,
        hypothesis="A falsifiable market hypothesis with no operational credentials.",
        params={"threshold": 1.0},
        proposed_by="test",
    )


def _window(app, sid, n, *, eligible=5, trades=3, wins=2, losses=1, gp="0.3", gl="0.1", fees="0.02", slippage="0.01"):
    start = 1_000_000 + n * 3600
    lab.record_window(
        app,
        sid,
        window_start=start,
        window_end=start + 3600,
        mode="SHADOW",
        opportunities=max(eligible, 10),
        eligible_opportunities=eligible,
        trades=trades,
        wins=wins,
        losses=losses,
        gross_profit=gp,
        gross_loss=gl,
        fees=fees,
        slippage_cost=slippage,
        largest_loss=gl,
    )


def test_ai_proposal_can_only_start_shadow(app):
    item = _register(app)
    assert item["status"] == "SHADOW"
    with lab.connect(app) as conn:
        row = conn.execute("SELECT status FROM strategy_lab_registry WHERE strategy_id=?", (item["strategy_id"],)).fetchone()
    assert row["status"] == "SHADOW"


def test_rejects_operational_secret_or_deployment_fields(app):
    with pytest.raises(ValueError):
        lab.register_strategy(
            app,
            name="Bad",
            family="TEST",
            source="AI_PROPOSED",
            hypothesis="bad",
            params={"private_key": "x"},
            proposed_by="test",
        )


def test_no_market_edge_is_not_treated_as_failure_to_trade(app):
    item = _register(app, "No Edge Yet")
    for n in range(3):
        _window(app, item["strategy_id"], n, eligible=0, trades=0, wins=0, losses=0, gp=0, gl=0, fees=0, slippage=0)
    decision = lab.evaluate_strategy(app, item["strategy_id"], mode="SHADOW")
    assert decision["action"] == "KEEP_SCANNING"
    assert decision["status"] == "PROBATION"
    assert "inactivity alone" in decision["reason"]


def test_overly_restrictive_strategy_is_reworked_not_rewarded_for_inactivity(app):
    item = _register(app, "Too Restrictive")
    for n in range(3):
        _window(app, item["strategy_id"], n, eligible=10, trades=1, wins=1, losses=0, gp="0.02", gl=0, fees="0.001", slippage="0.001")
    decision = lab.evaluate_strategy(app, item["strategy_id"], mode="SHADOW")
    assert decision["action"] == "REWORK_FILTERS"
    assert decision["status"] == "REWORK"


def test_money_weighted_losing_strategy_is_replaced_even_with_more_wins(app):
    item = _register(app, "Many Small Wins One Large Loss")
    # 9 wins, 3 losses overall, but the money-weighted result is negative.
    for n in range(3):
        _window(
            app,
            item["strategy_id"],
            n,
            eligible=5,
            trades=4,
            wins=3,
            losses=1,
            gp="0.09",
            gl="0.20",
            fees="0.01",
            slippage="0.01",
        )
    decision = lab.evaluate_strategy(app, item["strategy_id"], mode="SHADOW")
    assert decision["metrics"]["wins"] > decision["metrics"]["losses"]
    assert float(decision["metrics"]["net_profit"]) < 0
    assert decision["action"] == "REPLACE_OR_REWORK"
    assert decision["status"] == "REPLACE"


def test_positive_net_strategy_becomes_candidate_not_live(app):
    item = _register(app, "Positive Candidate")
    for n in range(3):
        _window(app, item["strategy_id"], n, eligible=5, trades=3, wins=2, losses=1, gp="0.30", gl="0.10", fees="0.02", slippage="0.01")
    decision = lab.evaluate_strategy(app, item["strategy_id"], mode="SHADOW")
    assert decision["action"] == "PROMOTION_CANDIDATE"
    assert decision["status"] == "PROMOTION_CANDIDATE"
    assert decision["live_auto_promote"] is False
    assert decision["changes_capital_or_safety"] is False


def test_insufficient_trade_sample_stays_in_testing(app):
    item = _register(app, "Sparse But Active")
    for n in range(3):
        _window(app, item["strategy_id"], n, eligible=2, trades=1, wins=1, losses=0, gp="0.02", gl=0, fees="0.001", slippage="0.001")
    decision = lab.evaluate_strategy(app, item["strategy_id"], mode="SHADOW")
    assert decision["action"] == "KEEP_TESTING"
    assert decision["status"] == "PROBATION"


def test_seeded_strategies_are_independent_non_leader_families(app):
    seeded = lab.seed_creative_hypotheses(app)
    names = {x["name"] for x in seeded}
    assert "Cross Venue Net Arbitrage" in names
    assert "Liquidity Confirmed Momentum" in names
    assert "Dislocation Mean Reversion" in names
    assert "Flow Acceleration" in names
    assert "New Liquidity Quality" in names
    assert "Learned Route Replication" in names
    assert all(x["status"] == "SHADOW" for x in seeded)


def test_record_window_rejects_forced_impossible_trade_counts(app):
    item = _register(app, "Count Invariant")
    with pytest.raises(ValueError):
        lab.record_window(
            app,
            item["strategy_id"],
            window_start=1,
            window_end=2,
            mode="SHADOW",
            opportunities=2,
            eligible_opportunities=1,
            trades=2,
        )
