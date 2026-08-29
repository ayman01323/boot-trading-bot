from __future__ import annotations

from . import solana_positive_edge_entry_gate_patch as _edge_gate
from . import solana_profit_guard_patch as _guard
# Correct reconstructed leader drawdown to closed-position semantics before the
# late leader-edge selector captures quality_metrics. Threshold values stay intact.
from . import solana_position_drawdown_patch as _position_drawdown  # noqa: F401
from . import solana_sibot as _sol

# Pre-invariant Solana leader-quality policy.  This layer intentionally stays at
# the audited moderate profile because trading_runtime_invariant_patch verifies it
# before any owner-specific final override is allowed to compose.
#
# SiLearn — 2026-08-29 12:23:54 BST — Subject: One-time rejection alerts +
# activate LP-unlocked LIVE revalidation.  The owner-approved historical 12:30
# profile is now applied only by solana_owner_changeset_4_patch AFTER the audited
# invariant succeeds.
_QUALITY_FLOOR_OVERRIDES = {
    "require_complete_history": "false",
    "min_win_rate_pct": "50",
    "min_profit_factor": "1.35",
    "recent_trade_window": "20",
    "min_recent_win_rate_pct": "55",
    "min_recent_profit_factor": "1.20",
    "max_leader_drawdown_pct": "30",
    "live_min_leader_median_return_pct": "2.5",
    "live_min_leader_recent_median_return_pct": "2.0",
    # Actual copied-LIVE performance protections remain unchanged at this stage.
    "min_copied_trades_for_guard": "2",
    "min_copied_win_rate_pct": "50",
    "min_copied_profit_factor": "1.50",
    "max_consecutive_copied_losses": "2",
    "leader_suspend_minutes": "1440",
}

_PREV_SETTINGS = _sol.settings


def settings_quality_restored(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))
    cfg.update(_QUALITY_FLOOR_OVERRIDES)
    cfg["solana_strategy_profile"] = str(cfg.get("solana_strategy_profile") or "") + "+MODERATE_LEADER_QUALITY"
    return cfg


def install():
    if getattr(_sol, "_leader_quality_restore_installed", False):
        return

    _sol.settings = settings_quality_restored

    # Keep the leader-event-time median-return / mint-realised-loss / platform
    # profit-factor circuit breaker. Only the leader median-return thresholds are
    # reduced above; the circuit-breaker path itself remains active.
    _sol.process_leader_event = _edge_gate.process_leader_event_positive_edge

    # Keep the hard copied-performance floors at this audited stage.  Change Set 4
    # is permitted to install the approved historical copied guard only after the
    # pre-existing runtime invariant has passed.
    _guard._copied_ok = _edge_gate.copied_ok_quarantine_first_loss

    _sol._leader_quality_restore_installed = True
    print(
        "[solana-leader-quality] profile=moderate history_complete=false "
        "win_rate>=50% pf>=1.35 recent_win_rate>=55% recent_pf>=1.20 "
        "drawdown<=30% median_return>=2.5% recent_median>=2.0% "
        "copied_win_rate>=50% copied_pf>=1.50 consecutive_loss_limit=2 "
        "leader_suspend=1440m execution_safety=unchanged"
    )


install()
