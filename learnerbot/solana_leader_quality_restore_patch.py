from __future__ import annotations

from . import solana_positive_edge_entry_gate_patch as _edge_gate
from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol

# solana_first_day_strategy_restore_patch deliberately loosened Solana leader-quality
# thresholds, dropped the leader-event median-return/mint-loss/platform-profit-factor
# circuit breaker (by rebinding process_leader_event straight to the unguarded base),
# and replaced the hard copied-performance floors with weaker configurable ones, in
# order to get trades flowing on a fresh wallet with thin history.
#
# This layer re-tightens leader *quality* back to the originally audited thresholds.
# It intentionally leaves every first-day *frequency/timing* setting (leaders_per_user,
# signal age, entry deterioration, stop-loss/take-profit) and the newer positive-
# executable-edge per-trade preflight (_validate_shadow_entry) untouched -- those are
# not being relaxed and are not the source of weak leader vetting.
_QUALITY_FLOOR_OVERRIDES = {
    "require_complete_history": "true",
    "min_win_rate_pct": "65",
    "min_profit_factor": "1.75",
    "recent_trade_window": "20",
    "min_recent_win_rate_pct": "65",
    "min_recent_profit_factor": "1.50",
    "max_leader_drawdown_pct": "20",
    "min_copied_trades_for_guard": "2",
    "min_copied_win_rate_pct": "50",
    "min_copied_profit_factor": "1.50",
    "max_consecutive_copied_losses": "2",
    "leader_suspend_minutes": "1440",
}

# Wrap whatever settings function is currently active (first-day's, at this import
# point) so operator LIVE/risk/execution settings and first-day timing keys still load;
# only the leader-quality floor keys above are overridden back to the audited values.
_PREV_SETTINGS = _sol.settings


def settings_quality_restored(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))
    cfg.update(_QUALITY_FLOOR_OVERRIDES)
    cfg["solana_strategy_profile"] = str(cfg.get("solana_strategy_profile") or "") + "+LEADER_QUALITY_RESTORED"
    return cfg


def install():
    if getattr(_sol, "_leader_quality_restore_installed", False):
        return

    _sol.settings = settings_quality_restored

    # Restore the leader-event-time median-return / mint-realised-loss / platform
    # profit-factor circuit breaker. This function object already exists and its
    # internal _PREV_PROCESS closure was correctly captured at its own original
    # import time (chained through the profit-first/partial-sell wrappers); we are
    # only re-pointing the active dispatch target back to it.
    _sol.process_leader_event = _edge_gate.process_leader_event_positive_edge

    # Restore the hard copied-performance floors (PF>=1.50, win-rate>=50%, enforced
    # via max() against configured values so they cannot be configured away) plus the
    # 24h first-copied-loss quarantine layered on top of them.
    _guard._copied_ok = _edge_gate.copied_ok_quarantine_first_loss

    _sol._leader_quality_restore_installed = True
    print(
        "[solana-leader-quality-restore] history_complete=true win_rate>=65% pf>=1.75 "
        "recent_win_rate>=65% recent_pf>=1.50 drawdown<=20% copied_win_rate>=50% "
        "copied_pf>=1.50 consecutive_loss_limit=2 leader_suspend=1440m "
        "leader_event_gate=median_return+mint_loss+platform_pf_breaker"
    )


install()
