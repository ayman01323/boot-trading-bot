from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from learnerbot import deep_trading_pipeline_repair_patch as repair
from learnerbot import fast_market
from learnerbot import full_power_scanner
from learnerbot import sibot
from learnerbot import sibot_alchemy_trace_progress_patch as trace
from learnerbot import solana_leader_edge_alignment_patch as leader
from learnerbot import solana_profit_guard_patch as guard


def _ctx(slug: str):
    return SimpleNamespace(config=SimpleNamespace(slug=slug))


def _metrics(**overrides):
    row = {
        "history_complete": True,
        "closed": 12,
        "win_rate": Decimal("60"),
        "profit_factor": Decimal("1.50"),
        "drawdown_pct": Decimal("20"),
        "recent_win_rate": Decimal("60"),
        "recent_profit_factor": Decimal("1.30"),
        "net": Decimal("1"),
        "median_return_pct": Decimal("6"),
        "recent_median_return_pct": Decimal("5"),
    }
    row.update(overrides)
    return row


def _cfg():
    return {
        "require_complete_history": "true",
        "live_min_leader_median_return_pct": "5",
        "live_min_leader_recent_median_return_pct": "4",
    }


def test_final_audited_function_identities_are_preserved():
    assert sibot.refresh_wallet_history is trace.refresh_wallet_history
    assert guard._historical_ok is leader.historical_ok


def test_active_fast_market_binding_is_repaired():
    assert fast_market.scan_full_power_hot_routes is repair._weighted_scan_full_power_hot_routes
    assert full_power_scanner.scan_full_power_hot_routes is repair._weighted_scan_full_power_hot_routes


def test_base_gets_larger_share_without_exceeding_total_budget():
    ctxs = [_ctx("base"), _ctx("bsc"), _ctx("ethereum"), _ctx("arbitrum"), _ctx("polygon")]
    budgets = repair._weighted_budgets(ctxs, 60, base_weight=4)
    assert sum(budgets.values()) == 60
    assert budgets["base"] > max(budgets[x] for x in ("bsc", "ethereum", "arbitrum", "polygon"))


def test_provider_pressure_detection_is_specific_to_throttle_errors():
    assert repair._provider_pressure({"error": "AlchemyHistoryError: HTTP 429"})
    assert repair._provider_pressure({"error": "compute units per second exceeded"})
    assert not repair._provider_pressure({"error": "invalid token contract"})
    assert not repair._provider_pressure({})


def test_mature_solana_profile_is_flexible_but_positive_and_complete():
    assert repair._adaptive_pre_quality_ok(_metrics(), _cfg())
    assert not repair._adaptive_pre_quality_ok(_metrics(net=Decimal("0")), _cfg())
    assert not repair._adaptive_pre_quality_ok(_metrics(history_complete=False), _cfg())
    assert not repair._adaptive_pre_quality_ok(_metrics(win_rate=Decimal("50")), _cfg())


def test_small_sample_requires_stronger_evidence():
    strong = _metrics(
        closed=6,
        win_rate=Decimal("75"),
        profit_factor=Decimal("2.0"),
        drawdown_pct=Decimal("10"),
        recent_win_rate=Decimal("70"),
        recent_profit_factor=Decimal("1.7"),
        median_return_pct=Decimal("8"),
        recent_median_return_pct=Decimal("6"),
    )
    weak = dict(strong)
    weak["win_rate"] = Decimal("60")
    assert repair._adaptive_pre_quality_ok(strong, _cfg())
    assert not repair._adaptive_pre_quality_ok(weak, _cfg())


def test_live_median_edge_floor_remains_authoritative():
    # The adaptive pre-gate can pass the mature statistical evidence, but the
    # existing leader.historical_ok must still reject a sub-LIVE median edge.
    metrics = _metrics(median_return_pct=Decimal("4.9"))
    assert repair._adaptive_pre_quality_ok(metrics, _cfg())
    assert not leader.historical_ok(metrics, _cfg())
