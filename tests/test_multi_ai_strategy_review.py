import json

import pytest

from learnerbot.multi_ai_strategy_review import (
    _extract_objective_metrics,
    _parse_json,
    _provider_result,
    _validate_consensus,
)


def test_parse_json_accepts_markdown_fence():
    assert _parse_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_objective_metrics_keep_money_weighted_pnl():
    report = {
        "solana_live": {
            "performance": {
                "closed_trades": 10,
                "wins": 7,
                "losses": 3,
                "gross_profit_sol": "0.15",
                "gross_loss_sol": "0.20",
                "net_sol": "-0.05",
                "profit_factor": "0.75",
                "profit_amount_exceeds_loss_amount": False,
                "largest_win_sol": "0.04",
                "largest_loss_sol": "0.12",
                "exit_reason_counts": {"STOP_LOSS": 2},
            },
            "exit_circuit_status_counts": {"FAILED": 1},
        }
    }
    metrics = _extract_objective_metrics(report)
    assert metrics["wins"] == 7
    assert metrics["losses"] == 3
    assert metrics["net"] == "-0.05"
    assert metrics["profit_factor"] == "0.75"
    assert metrics["profit_amount_exceeds_loss_amount"] is False


def test_provider_rejects_model_that_requests_live_changes():
    def fake_call(*args, **kwargs):
        return {"no_live_changes": False}, {"model": "fake"}

    result = _provider_result("fake", fake_call, "prompt")
    assert result["ok"] is False
    assert "no_live_changes" in result["error"]


def test_consensus_requires_live_auto_deploy_false():
    with pytest.raises(RuntimeError, match="live_auto_deploy"):
        _validate_consensus(
            {
                "live_auto_deploy": True,
                "draft_pr_only": True,
                "implementation_candidate": False,
                "status": "NO_CHANGE",
            },
            successful_reviewers=3,
        )


def test_consensus_requires_draft_pr_only():
    with pytest.raises(RuntimeError, match="draft_pr_only"):
        _validate_consensus(
            {
                "live_auto_deploy": False,
                "draft_pr_only": False,
                "implementation_candidate": False,
                "status": "NO_CHANGE",
            },
            successful_reviewers=3,
        )


def test_strategy_optimisation_requires_two_reviewers():
    with pytest.raises(RuntimeError, match="two independent reviewers"):
        _validate_consensus(
            {
                "live_auto_deploy": False,
                "draft_pr_only": True,
                "implementation_candidate": True,
                "status": "CODE_CHANGE_CANDIDATE",
            },
            successful_reviewers=1,
        )


def test_two_reviewers_can_only_create_candidate_not_live_deploy():
    _validate_consensus(
        {
            "live_auto_deploy": False,
            "draft_pr_only": True,
            "implementation_candidate": True,
            "status": "CODE_CHANGE_CANDIDATE",
        },
        successful_reviewers=2,
    )
