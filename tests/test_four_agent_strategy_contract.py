from __future__ import annotations

from copy import deepcopy

from learnerbot.three_agent_strategy_contract import enforce_master_policy, validate_agent_report


CYCLE = "e56e54510e47-2026082108-1234abcd"
SOURCE = "e56e54510e47aba1e85c24cfc4a5f7434f2b04ff"
EVIDENCE = "a" * 64


def _agent(provider: str) -> dict:
    return {
        "schema_version": 1,
        "provider": provider,
        "cycle_id": CYCLE,
        "source_commit": SOURCE,
        "evidence_sha256": EVIDENCE,
        "scope": "MULTI_AGENT_STRATEGY_REVIEW",
        "status": "CHANGES_PROPOSED",
        "summary": "review",
        "proposals": [
            {
                "id": f"{provider}-1",
                "action": "SHADOW_MORE",
                "strategy": "test-strategy",
                "chain_scope": ["SOLANA", "EVM"],
                "evidence": [{"path": "learnerbot/strategy_lab.py", "detail": "code evidence"}],
                "rationale": "test",
                "expected_effect": "test",
                "downside_risk": "test",
                "shadow_test": "Run in SHADOW and stop if net P&L is non-positive after costs.",
                "minimum_observation": "100 opportunities",
                "confidence": 0.9,
                "suggested_files": ["learnerbot/strategy_lab.py"],
            }
        ],
        "rejected_ideas": [],
        "evidence_gaps": [],
        "review_only": True,
        "no_live_changes": True,
    }


def _master(*, agents: list[str], four_agent_evidence: bool) -> dict:
    return {
        "schema_version": 1,
        "cycle_id": CYCLE,
        "source_commit": SOURCE,
        "evidence_sha256": EVIDENCE,
        "status": "DRAFT_SHADOW_CHANGE",
        "summary": "test",
        "four_agent_evidence": four_agent_evidence,
        "decisions": [
            {
                "finding_id": "f1",
                "source_proposal_ids": [f"{name}:p1" for name in agents],
                "action": "SHADOW_MORE",
                "strategy": "test-strategy",
                "disposition": "ACCEPT",
                "reason": "supported",
                "confidence": 0.91,
                "supporting_agents": agents,
                "risk_class": "LOW",
                "shadow_only": True,
                "allowed_files": ["learnerbot/strategy_lab.py"],
                "required_tests": ["pytest -q tests/test_strategy_lab.py"],
            }
        ],
        "implementation_allowed": False,
        "live_auto_deploy": False,
        "draft_pr_only": True,
    }


def test_claude_is_a_valid_independent_strategy_provider() -> None:
    report = _agent("claude")
    assert validate_agent_report(
        report,
        provider="claude",
        cycle_id=CYCLE,
        source_commit=SOURCE,
        evidence_sha256=EVIDENCE,
    ) is report


def test_legacy_three_agent_master_cannot_auto_code_before_claude_cycle_completes() -> None:
    gated = enforce_master_policy(_master(agents=["gpt", "gemini", "copilot"], four_agent_evidence=False))
    assert gated["implementation_allowed"] is False
    assert gated["policy_accepted_count"] == 0
    reasons = gated["decisions"][0]["policy_reasons"]
    assert any("four-agent" in reason.lower() for reason in reasons)


def test_three_of_four_support_is_enough_after_complete_four_agent_evidence() -> None:
    gated = enforce_master_policy(_master(agents=["gpt", "gemini", "claude"], four_agent_evidence=True))
    assert gated["implementation_allowed"] is True
    assert gated["policy_accepted_count"] == 1
    assert gated["policy"]["minimum_independent_agents"] == 3
    assert set(gated["policy"]["required_reviewers"]) == {"gpt", "gemini", "copilot", "claude"}


def test_two_of_four_support_remains_ineligible() -> None:
    gated = enforce_master_policy(_master(agents=["gpt", "claude"], four_agent_evidence=True))
    assert gated["implementation_allowed"] is False
    assert gated["policy_accepted_count"] == 0
