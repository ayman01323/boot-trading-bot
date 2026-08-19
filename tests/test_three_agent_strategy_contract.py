from __future__ import annotations

from learnerbot.three_agent_strategy_contract import (
    enforce_master_policy,
    strategy_auto_path_allowed,
    validate_agent_report,
)


def _agent(provider="gpt"):
    return {
        "schema_version": 1,
        "provider": provider,
        "cycle_id": "cycle-1",
        "source_commit": "abc",
        "evidence_sha256": "e" * 64,
        "scope": "THREE_AGENT_STRATEGY_REVIEW",
        "status": "CHANGES_PROPOSED",
        "summary": "test",
        "proposals": [{
            "id": "p1",
            "action": "IMPROVE",
            "strategy": "Liquidity Confirmed Momentum",
            "chain_scope": ["SOLANA", "EVM"],
            "evidence": [{"path": "strategy_lab.shadow_execution", "detail": "bounded evidence"}],
            "rationale": "test",
            "expected_effect": "test",
            "downside_risk": "test",
            "shadow_test": "Compare out-of-sample net P&L and profit factor before and after.",
            "minimum_observation": "3 windows / 8 trades",
            "confidence": 0.9,
            "suggested_files": ["learnerbot/cross_chain_strategy_signals.py"],
        }],
        "rejected_ideas": [],
        "evidence_gaps": [],
        "review_only": True,
        "no_live_changes": True,
    }


def _master(**overrides):
    row = {
        "finding_id": "s1",
        "source_proposal_ids": ["gpt:p1", "gemini:p1"],
        "action": "IMPROVE",
        "strategy": "Liquidity Confirmed Momentum",
        "disposition": "ACCEPT",
        "reason": "Two agents and measured shadow evidence support a bounded change.",
        "confidence": 0.91,
        "supporting_agents": ["gpt", "gemini"],
        "risk_class": "LOW",
        "shadow_only": True,
        "allowed_files": ["learnerbot/cross_chain_strategy_signals.py", "tests/test_cross_chain_strategy_signals.py"],
        "required_tests": ["pytest -q tests/test_cross_chain_strategy_signals.py"],
    }
    row.update(overrides)
    return {
        "schema_version": 1,
        "cycle_id": "cycle-1",
        "source_commit": "abc",
        "evidence_sha256": "e" * 64,
        "status": "DRAFT_SHADOW_CHANGE",
        "summary": "test",
        "decisions": [row],
        "implementation_allowed": False,
        "live_auto_deploy": False,
        "draft_pr_only": True,
    }


def test_agent_contract_requires_same_evidence_and_shadow_test():
    validate_agent_report(
        _agent(), provider="gpt", cycle_id="cycle-1", source_commit="abc", evidence_sha256="e" * 64
    )


def test_strategy_allowlist_excludes_live_and_deployment_paths():
    assert strategy_auto_path_allowed("learnerbot/cross_chain_strategy_signals.py")
    assert strategy_auto_path_allowed("tests/test_strategy_lab.py")
    assert not strategy_auto_path_allowed("learnerbot/live_executor.py")
    assert not strategy_auto_path_allowed("learnerbot/solana_live_patch.py")
    assert not strategy_auto_path_allowed(".github/workflows/deploy-vps.yml")
    assert not strategy_auto_path_allowed("CSVbot/auto_trading_settings.csv")


def test_two_agent_low_risk_shadow_change_can_be_draft_candidate():
    gated = enforce_master_policy(_master())
    assert gated["implementation_allowed"] is True
    assert gated["status"] == "DRAFT_SHADOW_CHANGE"
    assert gated["decisions"][0]["policy_eligible"] is True


def test_single_agent_or_high_risk_or_live_scope_is_blocked():
    one = enforce_master_policy(_master(supporting_agents=["gpt"]))
    assert one["implementation_allowed"] is False
    high = enforce_master_policy(_master(risk_class="HIGH"))
    assert high["implementation_allowed"] is False
    assert high["status"] == "HUMAN_REVIEW_REQUIRED"
    live = enforce_master_policy(_master(shadow_only=False))
    assert live["implementation_allowed"] is False
    assert live["status"] == "HUMAN_REVIEW_REQUIRED"


def test_replace_and_asset_request_may_be_recommended_but_not_auto_coded():
    replace = enforce_master_policy(_master(action="REPLACE"))
    assert replace["implementation_allowed"] is False
    request = enforce_master_policy(_master(action="ASSET_REQUEST"))
    assert request["implementation_allowed"] is False
