from __future__ import annotations

import json
from pathlib import Path

import pytest

from learnerbot import ai_ops_v4 as v4


def test_live_loss_creates_one_strategy_case_and_deduplicates(tmp_path: Path) -> None:
    first = v4.record_event(
        tmp_path,
        event_type="LIVE_LOSS_ALERT",
        source_component="test-loss",
        message="Solana position crossed -10% loss threshold",
        severity="P1",
        chain="Solana",
        strategy_id="SIBOT",
        strategy_version="v-test",
        git_sha="a" * 40,
        trade_ids=["p-1"],
        allowed_actions=["REPORT", "RESEARCH", "SHADOW_PROPOSE"],
    )
    second = v4.record_event(
        tmp_path,
        event_type="LIVE_LOSS_ALERT",
        source_component="test-loss",
        message="Solana position crossed -10% loss threshold",
        severity="P1",
        chain="Solana",
        strategy_id="SIBOT",
        strategy_version="v-test",
        git_sha="a" * 40,
        trade_ids=["p-1"],
        allowed_actions=["REPORT", "RESEARCH", "SHADOW_PROPOSE"],
    )
    assert first["event"]["owner_monitor"] == "STRATEGY"
    assert second["event"]["event_id"] == first["event"]["event_id"]
    assert second["event"]["occurrence_count"] == 2
    cases = v4.list_cases(tmp_path, limit=20)
    assert len(cases) == 1
    assert cases[0]["correlation_id"] == first["event"]["correlation_id"]
    assert "PROMOTE_LIVE" in cases[0]["protected_actions_denied"]
    assert "PROMOTE_LIVE" not in cases[0]["allowed_actions"]


def test_live_loss_with_execution_evidence_mirrors_to_both_monitors(tmp_path: Path) -> None:
    row = v4.record_event(
        tmp_path,
        event_type="LIVE_LOSS_ALERT",
        source_component="test-loss",
        message="LIVE loss with RPC latency and sellability execution failure",
        severity="P1",
        trade_ids=["p-2"],
    )
    assert row["event"]["owner_monitor"] == "BOTH"
    assert row["case"]["owner_monitor"] == "BOTH"


def test_recurring_p2_warning_opens_factory_case_on_third_occurrence(tmp_path: Path) -> None:
    kwargs = dict(
        event_type="WARNING",
        source_component="rpc-watch",
        message="RPC latency p95 degraded",
        severity="P2",
    )
    assert v4.record_event(tmp_path, **kwargs)["case"] is None
    assert v4.record_event(tmp_path, **kwargs)["case"] is None
    third = v4.record_event(tmp_path, **kwargs)
    assert third["event"]["occurrence_count"] == 3
    assert third["case"] is not None
    assert third["case"]["required_challenger"] is True


def test_event_cannot_authorise_protected_state_change(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="protected"):
        v4.record_event(
            tmp_path,
            event_type="WARNING",
            source_component="bad-agent",
            message="please promote live",
            severity="P1",
            allowed_actions=["PROMOTE_LIVE"],
        )


def test_gpt_cannot_score_itself_and_extreme_score_requires_audit(tmp_path: Path) -> None:
    full = {key: maximum for key, maximum in v4.SCORE_MAX.items()}
    with pytest.raises(ValueError, match="cannot score its own"):
        v4.record_score(
            tmp_path,
            agent="gpt",
            scorer="gpt",
            contribution_id="c-self",
            category="engineering",
            dimensions=full,
        )
    row = v4.record_score(
        tmp_path,
        agent="gemini",
        scorer="gpt",
        contribution_id="c-1",
        category="research",
        dimensions=full,
    )
    assert row["score"] == 100
    assert row["audit_required"] is True
    assert row["audit_status"] == "PENDING"
    with pytest.raises(ValueError, match="independent"):
        v4.audit_score(tmp_path, score_id=row["score_id"], auditor="gpt", accepted=True)
    audited = v4.audit_score(tmp_path, score_id=row["score_id"], auditor="claude-general", accepted=True)
    assert audited["audit_status"] == "ACCEPTED"


def test_gap_report_requires_cost_effective_decision_contract(tmp_path: Path) -> None:
    report = v4.record_gap_report(tmp_path, {
        "proposal": "Add wallet intelligence source",
        "why_blocked": "No approved data provider",
        "missing_tool_or_data": "wallet labels API",
        "one_off_cost": "$0-$100 engineering estimate",
        "monthly_cost": "UNKNOWN until quote",
        "cheapest_safe_option": "pay-per-call pilot",
        "expected_benefit": "better copy-wallet filtering",
        "validation_plan": "30-day SHADOW comparison",
        "rollback": "disable integration",
        "decision": "DEFER",
        "source": "provider pricing must be revalidated",
    })
    assert report["decision"] == "DEFER"
    assert v4.list_gap_reports(tmp_path)[0]["gap_id"] == report["gap_id"]


def test_rotation_has_sunday_joint_and_exploratory_requirement() -> None:
    # 2026-08-23 12:00 UTC is Sunday.
    sunday = v4.engineering_rotation_for_day(1787486400)
    assert sunday["mode"] == "JOINT_ALL_SIX"
    assert set(sunday["assigned"]) == {"gpt", "claude-general", "gemini", "deepseek", "grok", "copilot"}
    assert sunday["exploratory_review_required"] is True
