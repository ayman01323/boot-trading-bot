from __future__ import annotations

import json
import time
from contextlib import closing

from . import profit_control_loop_patch as _profit_control
from . import solana_live_patch as _live
from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol

# Restore the Solana trading-policy behaviour that was active around the first
# profitable confirmed-fast-lane period, while retaining all later execution,
# accounting, liquidity, simulation, reserve, deduplication and circuit-breaker
# protections.  The reference commit is the confirmed-fast-lane strategy state
# immediately before the later profit-first / immediate-leader-exit strategy
# rewrites were introduced.
FIRST_DAY_REFERENCE_COMMIT = "f0ca88450fe96a316dc15e676fab1e36c1137285"

# These are the effective balanced-frequency strategy values present in that
# reference state.  They intentionally control selection/timing/exit policy only.
# They do NOT change LIVE arming, trade size, reserve, signing, simulation,
# execution fee/impact caps, wallet binding, transaction validation or circuit
# breakers added later.
FIRST_DAY_STRATEGY_TARGETS = {
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
    "discovery_blocks_per_cycle": "4",
    "discovery_interval_seconds": "10",
    "candidate_limit": "150",
    "history_max_signatures": "400",
    "history_refresh_hours": "8",
    "leader_poll_seconds": "4",
    "position_poll_seconds": "10",
    "stop_loss_pct": "10",
    "take_profit_pct": "25",
    "leader_exit_loss_cap_pct": "2",
    "break_even_trigger_pct": "5",
    "break_even_floor_pct": "0.25",
    "trailing_trigger_pct": "10",
    "trailing_gap_pct": "4",
    "max_hold_hours": "24",
    "mirror_partial_sells": "true",
}

# profit_control_loop captured the normal Solana settings function before the
# later strategy wrappers were installed.  Use it as the base so current operator
# LIVE/risk/execution settings still load, then overlay only the first-day strategy
# fields above.
_BASE_SETTINGS = _profit_control._PREV_SETTINGS


def settings_first_day_strategy(app) -> dict:
    cfg = dict(_BASE_SETTINGS(app))
    cfg.update(FIRST_DAY_STRATEGY_TARGETS)
    cfg["solana_strategy_profile"] = "FIRST_DAY_CONFIRMED_FAST_LANE"
    cfg["solana_strategy_reference_commit"] = FIRST_DAY_REFERENCE_COMMIT
    return cfg


def copied_ok_first_day(app, tid, wallet, cfg):
    """Use the first-day copied-leader evidence thresholds.

    Keep the corrected current P&L/accounting implementation underneath, but do
    not impose the later 2-trade hard PF=1.50 gate, first-loss 24h quarantine or
    platform/mint amount gates.  This reproduces the earlier strategy threshold
    logic: after the configured sample, require configured copied win rate/PF and
    cool down only after the configured consecutive-loss streak.
    """
    m = _guard._copied_metrics(app, tid, wallet)
    key = f"sol_profit_guard_suspend:{tid}:{wallet}"
    now = int(time.time())

    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        raw = _sol._state(conn, key, "") or ""
        try:
            state = json.loads(raw) if raw else {}
        except Exception:
            state = {}
        until = int(state.get("until") or 0)
        seen = int(state.get("latest_closed_at") or 0)
        if now < until:
            return False

        limit = max(1, _sol._int(cfg.get("max_consecutive_copied_losses"), 3))
        latest = int(m.get("latest_closed_at") or 0)
        if int(m.get("consecutive_losses") or 0) >= limit and latest and latest != seen:
            until = now + max(5, _sol._int(cfg.get("leader_suspend_minutes"), 180)) * 60
            _sol._set_state(
                conn,
                key,
                json.dumps({"until": until, "latest_closed_at": latest}),
            )
            return False

    min_copied = max(1, _sol._int(cfg.get("min_copied_trades_for_guard"), 5))
    if int(m.get("closed") or 0) >= min_copied:
        if _sol._dec(m.get("win_rate"), 0) < _sol._dec(cfg.get("min_copied_win_rate_pct"), 40):
            return False
        if _sol._dec(m.get("profit_factor"), 0) < _sol._dec(cfg.get("min_copied_profit_factor"), 1):
            return False
    return True


def install():
    if getattr(_sol, "_first_day_strategy_restore_installed", False):
        return

    # Restore policy behaviour only.  The live executor and close path remain the
    # current audited/safe implementations installed by the later safety patches.
    _sol.settings = settings_first_day_strategy
    _guard._copied_ok = copied_ok_first_day
    _sol.process_leader_event = _live.process_leader_event

    _sol._first_day_strategy_restore_installed = True
    print(
        "[solana-first-day-strategy] restored=true reference=%s leaders=5 "
        "signal_age=30s roundtrip=3%% entry_deterioration=2%% stop=10%% tp=25%% "
        "leader_sell=original_gated execution_safety=current"
        % FIRST_DAY_REFERENCE_COMMIT[:12]
    )


install()
