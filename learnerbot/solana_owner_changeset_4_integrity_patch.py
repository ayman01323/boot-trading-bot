from __future__ import annotations

"""Fail-closed integrity gate for owner-approved Change Set 4.

Approval timestamp: 2026-08-29T10:38:58Z (2026-08-29 11:38:58 BST)
Subject: Learner historical BUY restore + 0.005 SOL/10 positions + 30+3 minute
full-exit + LP conditional revalidation.

The normal final_runtime_integrity_patch runs first and proves the pre-existing
safety stack.  Change Set 4 is then composed, and this second gate proves that its
late wrappers are complete and that the protected close/stuck-liquidity layers
remain directly underneath them.
"""

from . import solana_exit_circuit_breaker_patch as _exit_circuit
from . import solana_leader_quality_restore_patch as _quality
from . import solana_liquidity_stuck_nonblocking_patch as _stuck
from . import solana_live_patch as _live
from . import solana_owner_changeset_4_patch as _owner
from . import solana_pool_risk_gate as _pool
from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_sibot as _sol


def composition_checks() -> dict[str, bool]:
    return {
        "changeset_id": _owner.CHANGESET_ID == "CHANGE_SET_4",
        "changeset_timestamp": _owner.CHANGESET_APPROVED_UTC == "2026-08-29T10:38:58Z",
        "historical_profile_outer": _sol.settings is _quality.settings_quality_restored,
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
        "checks=%d rollback_manifest=docs/change-control/2026-08-29T103858Z-change-set-4.md"
        % len(checks)
    )


install()
