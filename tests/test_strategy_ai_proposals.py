from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from learnerbot.strategy_ai_proposals import (
    register_ai_strategy_payload,
    register_ai_strategy_proposal,
    validate_ai_strategy_proposal,
)
from learnerbot.strategy_lab import connect


@dataclass
class App:
    data_dir: str


@pytest.fixture()
def app(tmp_path: Path) -> App:
    return App(str(tmp_path))


def proposal(name="Independent Flow Breakout"):
    return {
        "name": name,
        "family": "flow_momentum",
        "hypothesis": "Independent flow acceleration plus executable liquidity can precede a short-lived continuation move.",
        "market_regime": "Increasing transaction flow with stable or improving executable liquidity.",
        "entry_logic": "Enter only after flow acceleration, price confirmation and positive expected edge after costs.",
        "exit_logic": "Use time/edge decay, target and bounded loss exits; do not depend on a leader wallet sale.",
        "data_required": ["price observations", "transaction flow", "liquidity", "executable quote"],
        "estimated_costs": "Include route fee, network fee, slippage reserve and measured execution leakage.",
        "failure_modes": ["crowded flow", "liquidity withdrawal", "late signal", "false breakout"],
        "shadow_test": "Replay the signal without submitting an order and compare hypothetical executable net P&L with the incumbent strategies.",
        "minimum_observation_windows": 6,
        "minimum_trades": 20,
        "falsification_conditions": ["net P&L <= 0 after costs", "profit factor <= 1", "loss magnitude dominates gains"],
        "differentiation": "Uses aggregate market flow rather than copying a selected leader wallet.",
    }


def test_valid_creative_proposal_is_registered_shadow(app):
    item = register_ai_strategy_proposal(app, proposal(), provider="gemini")
    assert item["source"] == "AI_PROPOSED"
    assert item["status"] == "SHADOW"
    assert item["proposed_by"] == "ai:gemini"
    with connect(app) as conn:
        row = conn.execute("SELECT status,source,params_json FROM strategy_lab_registry WHERE strategy_id=?", (item["strategy_id"],)).fetchone()
    assert row["status"] == "SHADOW"
    assert row["source"] == "AI_PROPOSED"
    assert "shadow_test" in row["params_json"]


def test_proposal_requires_falsifiable_details():
    bad = proposal()
    del bad["falsification_conditions"]
    with pytest.raises(ValueError):
        validate_ai_strategy_proposal(bad)


def test_proposal_rejects_executable_or_secret_material():
    bad = proposal()
    bad["entry_logic"] = "Use os.system('curl secret') and API_KEY to deploy it"
    with pytest.raises(ValueError):
        validate_ai_strategy_proposal(bad)


def test_payload_is_bounded_and_invalid_idea_does_not_block_valid_one(app):
    good = proposal("Good One")
    bad = proposal("Bad One")
    bad["minimum_trades"] = 0
    payload = {"new_strategy_hypotheses": [good, bad, proposal("Good Two"), proposal("Good Three"), proposal("Too Many")]}
    result = register_ai_strategy_payload(app, payload, provider="copilot")
    assert len(result["registered"]) == 3
    assert len(result["rejected"]) == 1
    assert result["truncated"] == 1
    assert result["live_auto_promote"] is False
    assert all(item["status"] == "SHADOW" for item in result["registered"])
