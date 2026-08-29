from __future__ import annotations

from . import solana_first_day_strategy_restore_patch as _first_day
from . import solana_positive_edge_entry_gate_patch as _edge_gate
from . import solana_profit_guard_patch as _guard
# Correct reconstructed leader drawdown to closed-position semantics before the
# late leader-edge selector captures quality_metrics.
from . import solana_position_drawdown_patch as _position_drawdown  # noqa: F401
from . import solana_sibot as _sol

# Owner-approved 2026-08-29 strategy policy:
# restore the Learner/SiBot selection/timing profile used around 2026-08-28
# 12:30 BST, while retaining the current execution, liquidity, simulation,
# reserve, signing, transaction-validation and circuit-breaker protections.
#
# Change Set 3 intentionally overrides three historical SELL values:
# - take profit: +15%
# - profitable max hold: 30 minutes
# - no planned/leader partial sells
# The unconditional 33-minute full-exit fallback is installed by the final owner
# policy patch because it needs access to the protected LIVE close path.
_OWNER_PROFILE_OVERRIDES = {
    "leaders_per_user": "5",
    "min_closed_trades": "5",
    "min_win_rate_pct": "50",
    "require_complete_history": "false",
    "min_profit_factor": "1.20",
    "recent_trade_window": "10",
    "min_recent_win_rate_pct": "50",
    "min_recent_profit_factor": "1.00",
    "max_leader_drawdown_pct": "30",
    "min_copied_trades_for_guard": "5",
    "min_copied_win_rate_pct": "40",
    "min_copied_profit_factor": "1.0",
    "max_consecutive_copied_losses": "3",
    "leader_suspend_minutes": "180",
    "max_signal_age_seconds": "30",
    "max_roundtrip_loss_pct": "3",
    "max_entry_deterioration_pct": "2",
    "discovery_blocks_per_cycle": "6",
    "discovery_interval_seconds": "15",
    "candidate_limit": "150",
    "history_max_signatures": "400",
    "history_refresh_hours": "8",
    "rpc_delay_seconds": "0.50",
    "leader_poll_seconds": "2",
    "position_poll_seconds": "10",
    "stop_loss_pct": "10",
    "take_profit_pct": "15",
    "leader_exit_loss_cap_pct": "2",
    "break_even_trigger_pct": "5",
    "break_even_floor_pct": "0.25",
    "trailing_trigger_pct": "10",
    "trailing_gap_pct": "4",
    "max_hold_hours": "0.5",
    "max_hold_force_exit_grace_minutes": "3",
    "mirror_partial_sells": "false",
    "live_min_leader_median_return_pct": "5.0",
    "live_min_leader_recent_median_return_pct": "4.0",
    "live_edge_recent_trade_window": "10",
    # Change Set 2 exceptions to the historical profile.
    "live_trade_sol": "0.005",
    "live_max_positions": "10",
}

_PREV_SETTINGS = _sol.settings


def settings_quality_restored(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))
    cfg.update(_OWNER_PROFILE_OVERRIDES)
    cfg["solana_strategy_profile"] = (
        str(cfg.get("solana_strategy_profile") or "")
        + "+OWNER_20260829_HISTORICAL_1230_WITH_EXIT_OVERRIDES"
    )
    return cfg


def install():
    if getattr(_sol, "_leader_quality_restore_installed", False):
        return

    _sol.settings = settings_quality_restored

    # Keep the current positive-edge / platform / mint entry wrapper. Its
    # thresholds are read from the restored profile above and every later
    # execution-safety layer remains composed underneath it.
    _sol.process_leader_event = _edge_gate.process_leader_event_positive_edge

    # Restore the copied-performance behaviour from the first-day/historical
    # strategy: evaluate after 5 copied closes, 40% win rate, PF 1.0, suspend
    # after 3 consecutive losses for 180 minutes. This removes the later hard
    # 2-trade/PF-1.50 re-tightening without touching execution safety.
    _guard._copied_ok = _first_day.copied_ok_first_day

    _sol._leader_quality_restore_installed = True
    print(
        "[solana-leader-quality] profile=owner-20260829-historical-1230 "
        "leaders=5 pf>=1.20 recent_win_rate>=50% recent_pf>=1.00 drawdown<=30% "
        "copied_guard=5 copied_win_rate>=40% copied_pf>=1.0 loss_streak=3 suspend=180m "
        "tp=15% max_hold_profit=30m partial_sells=false live_trade=0.005 max_positions=10 "
        "execution_safety=unchanged"
    )


install()
