from __future__ import annotations

import json

import pytest

from learnerbot.weekly_bug_audit_contract import (
    enforce_master_policy,
    extract_marked_json,
    protected_path,
    validate_agent_report,
    validate_master_decision,
)


SHA = "a" * 40


def agent_report(provider="gpt"):
    return {
        "schema_version": 1,
        "provider": provider,
        "source_commit": SHA,
        "scope": "FULL_REPOSITORY_BUG_AUDIT",
        "status": "ISSUES_FOUND",
        "summary": "one issue",
        "findings": [
            {
                "id": "BUG-1",
                "severity": "P2",
                "category": "CORRECTNESS",
                "title": "Example bug",
                "evidence": [{"file": "learnerbot/example.py", "line": 12, "symbol": "run", "detail": "branch is inverted"}],
                "impact": "wrong result",
                "root_cause": "inverted condition",
                "corrective_action": "fix condition and test it",
                "tests_required": ["pytest tests/test_example.py"],
                "confidence": 0.95,
                "deterministic_evidence": True,
            }
        ],
        "refusals_or_limits": [],
        "report_only": True,
        "no_live_changes": True,
    }


def master_row(**overrides):
    row = {
        "finding_id": "ROOT-1",
        "source_finding_ids": ["gpt:BUG-1", "gemini:G-2"],
        "severity": "P2",
        "title": "Example bug",
        "disposition": "ACCEPT",
        "reason": "two agents cite same deterministic code defect",
        "confidence": 0.95,
        "supporting_agents": ["gpt", "gemini"],
        "deterministic_evidence": False,
        "risk_class": "LOW",
        "allowed_files": ["learnerbot/example.py", "tests/test_example.py"],
        "required_tests": ["pytest tests/test_example.py"],
    }
    row.update(overrides)
    return row


def master_decision(*rows):
    return {
        "schema_version": 1,
        "source_commit": SHA,
        "status": "DRAFT_FIX" if rows else "NO_ACTION",
        "summary": "weekly adjudication",
        "decisions": list(rows),
        "implementation_allowed": False,
        "live_auto_deploy": False,
        "draft_pr_only": True,
    }


def test_valid_agent_report_contract():
    validate_agent_report(agent_report(), provider="gpt", source_commit=SHA)


def test_agent_report_requires_file_evidence():
    report = agent_report()
    report["findings"][0]["evidence"] = []
    with pytest.raises(ValueError, match="evidence"):
        validate_agent_report(report, provider="gpt", source_commit=SHA)


def test_marked_json_extraction():
    payload = agent_report("gemini")
    text = "header\nWEEKLY_AUDIT_JSON_BEGIN\n" + json.dumps(payload) + "\nWEEKLY_AUDIT_JSON_END\n"
    assert extract_marked_json(text)["provider"] == "gemini"


def test_master_policy_accepts_high_confidence_two_agent_low_risk_fix():
    decision = master_decision(master_row())
    validate_master_decision(decision, source_commit=SHA)
    gated = enforce_master_policy(decision)
    assert gated["implementation_allowed"] is True
    assert gated["policy_accepted_count"] == 1
    assert gated["decisions"][0]["policy_eligible"] is True


def test_master_policy_allows_single_agent_only_with_deterministic_evidence():
    decision = master_decision(master_row(supporting_agents=["gpt"], deterministic_evidence=True))
    gated = enforce_master_policy(decision)
    assert gated["implementation_allowed"] is True

    decision2 = master_decision(master_row(supporting_agents=["gpt"], deterministic_evidence=False))
    gated2 = enforce_master_policy(decision2)
    assert gated2["implementation_allowed"] is False
    assert "two independent agents" in " ".join(gated2["decisions"][0]["policy_reasons"])


def test_master_policy_blocks_low_confidence_and_p0():
    low = enforce_master_policy(master_decision(master_row(confidence=0.84)))
    assert low["implementation_allowed"] is False

    p0 = enforce_master_policy(master_decision(master_row(severity="P0", confidence=0.99)))
    assert p0["implementation_allowed"] is False
    assert p0["status"] == "HUMAN_REVIEW_REQUIRED"


def test_master_policy_blocks_protected_file():
    assert protected_path(".github/workflows/deploy-vps.yml")
    decision = master_decision(master_row(allowed_files=[".github/workflows/deploy-vps.yml"]))
    gated = enforce_master_policy(decision)
    assert gated["implementation_allowed"] is False
    assert gated["status"] == "HUMAN_REVIEW_REQUIRED"


def test_rejected_or_deferred_items_never_implement():
    rejected = enforce_master_policy(master_decision(master_row(disposition="REJECT")))
    assert rejected["implementation_allowed"] is False
    assert rejected["decisions"][0]["policy_eligible"] is False

    deferred = enforce_master_policy(master_decision(master_row(disposition="DEFER")))
    assert deferred["implementation_allowed"] is False
    assert deferred["decisions"][0]["policy_eligible"] is False
