from __future__ import annotations

from pathlib import Path

import pytest

from learnerbot import ai_agent_target_score as score


def _base_scores():
    return {
        "correctness": 18,
        "evidence": 14,
        "marginal_value": 8,
        "actionability": 9,
        "collaboration": 4,
        "cost_efficiency": 4,
        "timeliness": 5,
    }


def test_provisional_economic_impact_is_capped_until_outcome(tmp_path: Path):
    row = score.record_score(
        tmp_path,
        agent="gemini",
        contribution_id="c1",
        scorer="gpt",
        category="strategy",
        scores=_base_scores(),
        economic={"net_edge": 15, "loss_prevention": 8, "target_quality": 7},
        outcome_resolved=False,
    )
    assert row["economic_total"] == 10
    assert row["atcs"] == 72
    assert row["audit_required"] is True


def test_outcome_resolved_can_earn_full_economic_impact(tmp_path: Path):
    row = score.record_score(
        tmp_path,
        agent="deepseek",
        contribution_id="c2",
        scorer="gpt",
        category="strategy",
        scores=_base_scores(),
        economic={"net_edge": 15, "loss_prevention": 8, "target_quality": 7},
        outcome_resolved=True,
    )
    assert row["economic_total"] == 30
    assert row["atcs"] == 92


def test_gpt_cannot_score_itself(tmp_path: Path):
    with pytest.raises(ValueError, match="cannot score its own"):
        score.record_score(
            tmp_path,
            agent="gpt",
            contribution_id="c3",
            scorer="gpt",
            category="engineering",
            scores=_base_scores(),
        )


def test_claude_general_and_coding_are_separate_score_identities(tmp_path: Path):
    score.register_pending(tmp_path, agent="claude-general", contribution_id="g1", category="strategy")
    score.register_pending(tmp_path, agent="claude-coding", contribution_id="c1", category="engineering")
    report = score.summary(tmp_path)
    assert report["agents"]["claude-general"]["pending_score"] == 1
    assert report["agents"]["claude-coding"]["pending_score"] == 1


def test_kimi_is_in_current_score_roster():
    assert "kimi" in score.LOGICAL_AGENTS


def test_value_assessment_never_auto_removes_agent(tmp_path: Path):
    for i in range(30):
        score.record_score(
            tmp_path,
            agent="grok",
            contribution_id=f"g{i}",
            scorer="gpt",
            category="strategy",
            scores={
                "correctness": 2,
                "evidence": 2,
                "marginal_value": 1,
                "actionability": 1,
                "collaboration": 1,
                "cost_efficiency": 1,
                "timeliness": 1,
            },
            economic={"net_edge": 0, "loss_prevention": 0, "target_quality": 0},
            outcome_resolved=True,
            created_at=1_800_000_000 + i,
        )
    row = score.record_value_assessment(
        tmp_path,
        agent="grok",
        assessor="claude-general",
        dimensions={
            "marginal_value_added": 10,
            "critical_specialization": 10,
            "independence_uniqueness": 10,
            "cost_efficiency": 10,
            "availability_reliability": 10,
        },
        evidence_window_days=90,
        material_outcomes=30,
        consecutive_weak_windows=2,
        ablation_passed=True,
        no_unique_critical_specialization=True,
        independently_audited=True,
        created_at=1_800_000_100,
    )
    assert row["band"] == "REMOVE CANDIDATE"
    assert row["removal_candidate_gate"] is True
    assert row["automatic_removal_allowed"] is False


def test_score_audit_must_be_non_originating(tmp_path: Path):
    score.record_score(
        tmp_path,
        agent="gemini",
        contribution_id="audit1",
        scorer="gpt",
        category="strategy",
        scores=_base_scores(),
    )
    with pytest.raises(ValueError, match="cannot audit its own"):
        score.audit_score(tmp_path, contribution_id="audit1", auditor="gemini")
    audit = score.audit_score(tmp_path, contribution_id="audit1", auditor="deepseek", status="CORRECTED", audited_score=65)
    assert audit["audited_score"] == 65


def test_telegram_score_patch_contains_master_menu_callback():
    source = Path("learnerbot/telegram_ai_target_score_patch.py").read_text(encoding="utf-8")
    assert "⭐ AI Target Scores" in source
    assert '"aiops:scores"' in source
    assert "No score can automatically remove an agent" in source
