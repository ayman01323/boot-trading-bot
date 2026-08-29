from __future__ import annotations

"""Fail-closed integrity gate for owner-approved Change Set 4.

Approval timestamp: 2026-08-29T10:38:58Z (2026-08-29 11:38:58 BST)
Subject: Learner historical BUY restore + 0.005 SOL/10 positions + 30+3 minute
full-exit + LP conditional revalidation.

The normal audited invariants run first against the pre-change moderate leader
quality layer. Change Set 4 then installs the owner profile as a final late-bound
wrapper, and this gate proves both stages are composed in the intended order.
"""

from . import solana_exit_circuit_breaker_patch as _exit_circuit
from . import solana_first_day_strategy_restore_patch as _first_day
from . import solana_leader_quality_restore_patch as _quality
from . import solana_liquidity_stuck_nonblocking_patch as _stuck
from . import solana_live_patch as _live
from . import solana_owner_changeset_4_exit_safety_patch as _exit_safety
from . import solana_owner_changeset_4_patch as _owner
from . import solana_pool_risk_gate as _pool
from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol


def composition_checks() -> dict[str, bool]:
    profile = _owner._OWNER_PROFILE_OVERRIDES
    return {
        "changeset_id": _owner.CHANGESET_ID == "CHANGE_SET_4",
        "changeset_timestamp": _owner.CHANGESET_APPROVED_UTC == "2026-08-29T10:38:58Z",
        "pre_invariant_quality_preserved": _owner._PREV_SETTINGS is _quality.settings_quality_restored,
        "historical_profile_final_outer": _sol.settings is _owner.settings_owner_changeset_4,
        "historical_profile_values": (
            profile.get("leaders_per_user") == "5"
            and profile.get("min_profit_factor") == "1.20"
            and profile.get("min_recent_win_rate_pct") == "50"
            and profile.get("min_recent_profit_factor") == "1.00"
            and profile.get("min_copied_trades_for_guard") == "5"
            and profile.get("min_copied_win_rate_pct") == "40"
            and profile.get("min_copied_profit_factor") == "1.0"
            and profile.get("max_consecutive_copied_losses") == "3"
            and profile.get("leader_suspend_minutes") == "180"
            and profile.get("take_profit_pct") == "15"
            and profile.get("max_hold_hours") == "0.5"
            and profile.get("mirror_partial_sells") == "false"
            and profile.get("live_trade_sol") == "0.005"
            and profile.get("live_max_positions") == "10"
        ),
        "owner_copied_guard_final": _guard._copied_ok is _first_day.copied_ok_first_day,
        "owner_trade_limit_outer": _live.live_limits is _owner.live_limits_owner_changeset_4,
        "owner_lp_revalidation_outer": _pool.evaluate_rugcheck is _owner.evaluate_rugcheck_lp_revalidation,
        "owner_capacity_eligibility_outer": _pool._eligible_live_users is _owner.eligible_live_users_owner_changeset_4,
        "owner_capacity_process_inner": _pool._PREV_PROCESS is _owner.process_leader_event_owner_changeset_4,
        "poolcheck_entry_outer": _live.process_leader_event is _pool.process_leader_event_with_pool_risk,
        "positive_edge_inner_poolcheck": _edge._PREV_PROCESS is _live.process_leader_event,
        "positive_edge_final_outer": _sol.process_leader_event is _edge.process_leader_event_positive_edge,
        "owner_33m_monitor_outer": _sol.monitor_positions is _owner.monitor_positions_owner_changeset_4,
        "liquidity_stuck_monitor_preserved": _owner._PREV_MONITOR is _stuck.monitor_positions_with_stuck_owner_resolution,
        "protected_close_preserved": _live._close_live is _exit_circuit.close_live_guarded,
        "timed_exit_safe_backoff": (
            _exit_safety.CHANGESET4_TIMED_EXIT_REASON
            in _exit_safety._emergency._LOSS_EXIT_REASONS
        ),
        "trade_size_exact": _owner.OWNER_LIVE_TRADE_SOL == _owner.Decimal("0.005"),
        "position_ceiling_exact": _owner.OWNER_MAX_LIVE_POSITIONS == 10,
        "force_exit_exact": _owner.OWNER_FORCE_EXIT_SECONDS == 33 * 60,
    }


def install() -> None:
    checks = composition_checks()
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Change Set 4 runtime integrity failed: " + ", ".join(failed))
    print(
        "[owner-changeset-4-integrity] OK approved=2026-08-29T10:38:58Z "
        "staged_profile=true checks=%d rollback_manifest=docs/change-control/2026-08-29T103858Z-change-set-4.md"
        % len(checks)
    )


install()

# SiLearn — 2026-08-29 12:23:54 BST — Subject: One-time rejection alerts.
# This reporting-only layer is intentionally composed only after Change Set 4's
# trading integrity gate succeeds. It does not alter any trading hook verified above.
from . import solana_reject_once_reporting_patch as _reject_once  # noqa: E402,F401

# SiLearn — 2026-08-29 13:28 BST — Subject: Downgrade LP lock/provider warnings to Telegram reference only.
# Loaded after the stamped Change Set 4 integrity proof. It changes only LP-specific
# RugCheck lock/provider classifications; all other PoolCheck/execution gates remain.
from . import solana_lp_warning_only_patch as _lp_warning_only  # noqa: E402,F401

# Explicit manual force-exit recovery only. Load this last, after the complete
# Change Set 4 integrity proof, so it can never weaken or mask the automatic
# duplicate-SELL guard verified above. Reverting the change removes this import
# and the isolated patch without altering any pre-existing exit implementation.
from . import solana_manual_force_exit_reconcile_patch as _manual_force_reconcile  # noqa: E402,F401
