from __future__ import annotations

"""Restore the 17-Aug Solana learner/copy policy for a tiny LIVE canary.

This is deliberately a *policy* rollback, not an execution rollback. The leader
selection and entry-decision behaviour are restored to the known-good 17-Aug
baseline immediately before the confirmed LIVE trades, while the current guarded
Jupiter executor, signed simulation, reserve enforcement, transaction validation,
fee/impact caps, wallet binding, durable dedupe, PoolCheck/stress-exit preflight,
position reconciliation and LIQUIDITY_STUCK non-blocking ownership remain active.
"""

from . import solana_execution_validation_patch as _validation
from . import solana_exit_circuit_breaker_patch as _exit_circuit
from . import solana_first_day_strategy_restore_patch as _first_day
from . import solana_liquidity_stuck_nonblocking_patch as _stuck
from . import solana_live_executor as _executor
from . import solana_live_patch as _live
from . import solana_preflight_cache_patch as _preflight
from . import solana_profit_guard_patch as _guard
from . import solana_simulated_reserve_guard_patch as _reserve
from . import solana_sibot as _sol
from . import solana_trade_diagnostics_patch as _diag

AUG17_REFERENCE_COMMIT = "a524c530e19a70f8b303dc9a3e65b3ac9596705e"

# Values from learnerbot/solana_sibot.py at the known-good pre-trade commit.
# Keep the canary to one non-stuck LIVE position. Trade amount and reserve are
# intentionally NOT changed here: the existing low-capital per-user controls stay
# authoritative (currently 0.0005 SOL trade / 0.005 SOL reserve in production).
AUG17_POLICY = {
    "lookback_days": "60",
    "discovery_blocks_per_cycle": "2",
    "discovery_interval_seconds": "15",
    "candidate_limit": "100",
    "history_max_signatures": "250",
    "history_refresh_hours": "12",
    "leaders_per_user": "2",
    "min_closed_trades": "5",
    "min_win_rate_pct": "50",
    "leader_poll_seconds": "5",
    "position_poll_seconds": "15",
    "max_signal_age_seconds": "30",
    "max_roundtrip_loss_pct": "3",
    "max_entry_deterioration_pct": "2",
    "stop_loss_pct": "10",
    "take_profit_pct": "25",
    "leader_exit_loss_cap_pct": "2.5",
    "break_even_trigger_pct": "5",
    "break_even_floor_pct": "0.10",
    "trailing_trigger_pct": "10",
    "trailing_gap_pct": "5",
    "max_hold_hours": "24",
    "mirror_partial_sells": "true",
    "live_max_positions": "1",
}

# The first-day module already captured the normal settings function before the
# later strategy-policy overlays. It still sees all current DEFAULTS added by
# execution/safety patches and all operator/per-user LIVE settings.
_BASE_SETTINGS = _first_day._BASE_SETTINGS


def settings_aug17_canary(app) -> dict:
    cfg = dict(_BASE_SETTINGS(app))
    cfg.update(AUG17_POLICY)
    cfg["solana_strategy_profile"] = "AUG17_KNOWN_GOOD_CANARY_CURRENT_EXECUTION_SAFETY"
    cfg["solana_strategy_reference_commit"] = AUG17_REFERENCE_COMMIT
    return cfg


def _verify_execution_safety() -> None:
    """Fail startup rather than allow a policy rollback to displace safety hooks."""
    checks = {
        "guarded_close": _live._close_live is _exit_circuit.close_live_guarded,
        "validated_buy": _executor.SolanaLiveExecutor.buy is _validation._buy_with_token_reconciliation,
        "validated_sell": _executor.SolanaLiveExecutor.sell is _validation._sell_with_token_reconciliation,
        "validated_swap": _executor.SolanaLiveExecutor.swap is _validation._swap_amounts_authoritative,
        "signed_simulation_reserve": _executor.SolanaLiveExecutor._simulate is _reserve._simulate_with_wallet_snapshot,
        "stuck_does_not_consume_capacity": _live._open_live_count is _stuck.open_live_count_without_verified_stuck,
        "stuck_monitor_stays_outer": _sol.monitor_positions is _stuck.monitor_positions_with_stuck_owner_resolution,
        "same_mint_still_blocked_by_open_inventory": callable(_sol._open_position),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Aug17 canary refused: execution safety composition changed: " + ", ".join(failed))


def _verify_canary_policy() -> None:
    checks = {
        "settings": _sol.settings is settings_aug17_canary,
        "historical_selector": _sol.refresh_rankings is _guard._PREV_REFRESH,
        "current_safe_preflight": _sol._validate_shadow_entry is _preflight.validate_entry_cached,
        "decision_diagnostics_outer": _sol.process_leader_event is _diag.process_leader_event,
        "live_event_inner": _diag._PREV_PROCESS is _live.process_leader_event,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Aug17 canary refused: policy composition changed: " + ", ".join(failed))


def install() -> None:
    if getattr(_sol, "_aug17_canary_restore_installed", False):
        return

    # Verify current execution composition before touching policy.
    _verify_execution_safety()

    # 1) Restore the exact broad positive-net ranking/leader selector captured
    # before the later PF/recent/drawdown/copied-performance selector wrappers.
    # It still requires positive net history, gross profit > gross loss, >=5 closes
    # and >=50% win rate, matching the 17-Aug source.
    _sol.settings = settings_aug17_canary
    _sol.refresh_rankings = _guard._PREV_REFRESH

    # 2) Restore the 17-Aug entry-decision route but retain today's preflight
    # hardening. The cached validator contains current PoolCheck/stress-exit checks
    # plus the original <=3% round-trip / <=2% deterioration requirements. We
    # remove only the later expected-return/median/PF policy gates for this canary.
    _sol._validate_shadow_entry = _preflight.validate_entry_cached

    # 3) Keep the current LIVE executor and all functions it calls. Re-attach the
    # read-only decision recorder around that foundational path so every
    # BUY/SELL/SKIP/REJECT remains diagnosable.
    _diag._PREV_PROCESS = _live.process_leader_event
    _sol.process_leader_event = _diag.process_leader_event

    _verify_execution_safety()
    _verify_canary_policy()
    _sol._aug17_canary_restore_installed = True
    print(
        "[solana-aug17-canary] restored=true reference=%s leaders=2 "
        "trade_size=existing-low-capital max_nonstuck_positions=1 "
        "roundtrip<=3%% deterioration<=2%% stop=10%% tp=25%% "
        "stuck_nonblocking=current execution_safety=current"
        % AUG17_REFERENCE_COMMIT[:12]
    )


install()
