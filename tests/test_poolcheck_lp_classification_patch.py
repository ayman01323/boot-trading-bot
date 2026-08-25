from __future__ import annotations

from learnerbot import poolcheck_lp_classification_patch as patch
from learnerbot import solana_pool_risk_gate as pool


def _cfg():
    return {
        "live_pool_rugcheck_hard_score": "70",
        "live_pool_min_lp_locked_pct": "50",
    }


def test_large_lp_unlocked_severe_is_shadow_only_not_structural_hard_block():
    result = patch.evaluate_rugcheck(
        {
            "score_normalised": 20,
            "lpLockedPct": 100,
            "risks": [{"name": "Large Amount of LP Unlocked", "level": "danger"}],
        },
        _cfg(),
    )
    assert result["decision"] == "SHADOW_ONLY"
    assert result["reason_code"] == "LP_CONCENTRATION_RISK"
    assert result["evidence"]["rugcheck_reclassified_from"] == "HARD_BLOCK"
    assert result["evidence"]["live_eligible"] is False
    # Legacy LIVE rejects any non-PASS decision; the classification correction
    # therefore increases SHADOW learning without making this LIVE-eligible.
    assert pool._severity(result) > 0


def test_structural_freeze_authority_remains_hard_block():
    result = patch.evaluate_rugcheck(
        {
            "score_normalised": 20,
            "lpLockedPct": 100,
            "risks": [{"name": "Freeze Authority still enabled", "level": "danger"}],
        },
        _cfg(),
    )
    assert result["decision"] == "HARD_BLOCK"
    assert result["reason_code"] == "TOKEN_SECURITY_SEVERE"


def test_unknown_severe_risk_remains_hard_block_fail_closed():
    result = patch.evaluate_rugcheck(
        {
            "score_normalised": 20,
            "lpLockedPct": 100,
            "risks": [{"name": "Unknown severe provider finding", "level": "severe"}],
        },
        _cfg(),
    )
    assert result["decision"] == "HARD_BLOCK"


def test_aggregate_high_risk_score_remains_hard_block():
    result = patch.evaluate_rugcheck(
        {
            "score_normalised": 80,
            "lpLockedPct": 100,
            "risks": [{"name": "Large Amount of LP Unlocked", "level": "danger"}],
        },
        _cfg(),
    )
    assert result["decision"] == "HARD_BLOCK"
    assert "score" in result["reason"].lower()


def test_liquidity_label_with_structural_description_stays_hard_block():
    result = patch.evaluate_rugcheck(
        {
            "score_normalised": 20,
            "lpLockedPct": 100,
            "risks": [{
                "name": "Large Amount of LP Unlocked",
                "level": "danger",
                "description": "freeze authority still enabled",
            }],
        },
        _cfg(),
    )
    assert result["decision"] == "HARD_BLOCK"
