from __future__ import annotations

"""Final fail-closed runtime composition check.

This module is imported *last* by learnerbot.__main__, after the late Alchemy,
recovery, leader-alignment, provenance and AI-runtime patches have composed.
It verifies the audited hooks and the Basic Engine v0 main-entry binding.
"""

from . import kimi_ai_health_roster_patch as _kimi_health  # noqa: F401

from . import auto_trader as _auto
from . import evm_pool_rug_gate as _evm_rug
from . import evm_transfer_native_hotfix_patch as _evm_transfer
from . import live_executor as _evm_live
from . import polygon_websocket_patch as _evm_ws
from . import sibot as _sibot
from . import sibot_alchemy_context_progress_patch as _context
from . import sibot_alchemy_retry_queue_patch as _retry
from . import sibot_alchemy_trace_progress_patch as _trace
from . import sibot_evm_worker_reliability_patch as _evm_reliability
from . import sibot_leader_quality_hard_floor_patch as _evm_quality
from . import sibot_legacy_error_sweep_patch as _legacy
from . import sibot_legacy_backlog_drainer_patch as _legacy_drainer
# Compose bounded endpoint failover inside the already-audited trace/progressive
# history wrapper. The final public refresher identity remains unchanged.
from . import sibot_alchemy_endpoint_pool_patch as _endpoint_pool
from . import solana_atomic_close_fallback_patch as _atomic
from . import solana_entry_capacity_reconcile_patch as _capacity
from . import solana_execution_efficiency_patch as _efficiency
from . import solana_execution_validation_patch as _validation
from . import solana_exit_circuit_breaker_patch as _exit_circuit
from . import solana_first_day_strategy_restore_patch as _first_day
from . import solana_leader_edge_alignment_patch as _leader_edge
from . import solana_liquidity_fail_closed_patch as _liquidity
from . import solana_liquidity_stuck_nonblocking_patch as _stuck
from . import solana_live_executor as _sol_exec
from . import solana_live_patch as _sol_live
from . import solana_live_position_scope_fix_patch as _scope
from . import solana_overhead_gate_correction_patch as _overhead
from . import solana_platform_recovery_reconcile_patch as _recovery
from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_preflight_cache_patch as _preflight
from . import solana_profit_guard_patch as _sol_guard
from . import solana_quote_execution_consistency_patch as _quote
from . import solana_simulated_reserve_guard_patch as _reserve
from . import solana_sibot as _sol
from . import telegram_ai_council_friendly_patch as _friendly
from . import telegram_ui as _telegram_ui
from . import transaction_audit_worker_patch as _audit_worker
from . import trade_strategy_provenance_patch as _provenance
from .basic_engine_v0 import main_patch as _basic_v0

# Tighten PoolCheck only after the existing EVM/Solana gates and quote cache have
# composed. This module can only reject entries; it never enables execution.
from . import poolcheck_rug_hardening_patch as _poolcheck_hardening  # noqa: E402,F401


def composition_checks() -> dict[str, bool]:
    """Return the exact final runtime identities that must remain authoritative."""
    return {
        "solana_close_circuit": _sol_live._close_live is _exit_circuit.close_live_guarded,
        "solana_order_fee_guard": _sol_exec.SolanaLiveExecutor._order is _efficiency.order_with_economic_caps,
        "solana_liquidity_fail_closed": _efficiency._validate_order is _liquidity.validate_order_fail_closed_on_unknown_liquidity,
        "solana_atomic_sell_fallback": _efficiency.sell_with_atomic_account_close is _atomic.sell_with_atomic_or_capped_legacy_fallback,
        "solana_swap_validation": _sol_exec.SolanaLiveExecutor.swap is _validation._swap_amounts_authoritative,
        "solana_sell_validation": _sol_exec.SolanaLiveExecutor.sell is _validation._sell_with_token_reconciliation,
        "solana_buy_validation": _sol_exec.SolanaLiveExecutor.buy is _validation._buy_with_token_reconciliation,
        "solana_buy_reserve_inner": _validation._PREV_BUY is _reserve._buy_with_simulated_reserve,
        "solana_simulation_reserve": _sol_exec.SolanaLiveExecutor._simulate is _reserve._simulate_with_wallet_snapshot,
        "solana_quote_executable": _sol.jupiter_quote is _quote.jupiter_quote_executable,
        "solana_positive_edge_outer": _sol._validate_shadow_entry is _first_day.validate_entry_positive_executable_edge,
        "solana_preflight_cache_inner": _first_day._PREV_VALIDATE is _preflight.validate_entry_cached,
        "solana_economic_gate": _sol_live._economic_entry_gate is _overhead._economic_entry_gate_reconciled,
        "solana_capacity_stuck_outer": _sol_live._open_live_count is _stuck.open_live_count_without_verified_stuck,
        "solana_capacity_verified_inner": _stuck._PREV_OPEN_COUNT is _capacity._verified_open_live_count,
        "solana_recovery_stuck_outer": _edge._platform_amount_gate is _stuck.platform_amount_gate_without_stuck_freeze,
        "solana_recovery_reconcile_inner": _stuck._PREV_PLATFORM_GATE is _recovery.platform_amount_gate,
        "solana_duplicate_guard_live_only": _sol._open_position is _scope._open_live_position,
        "solana_leader_broader_selector": _sol.refresh_rankings is _leader_edge.refresh_rankings,
        "solana_leader_edge_metrics": _sol_guard.quality_metrics is _leader_edge.quality_metrics,
        "solana_leader_edge_gate": _sol_guard._historical_ok is _leader_edge.historical_ok,
        "solana_stuck_monitor_outer": _sol.monitor_positions is _stuck.monitor_positions_with_stuck_owner_resolution,
        "evm_alchemy_refresh": _sibot.refresh_wallet_history is _trace.refresh_wallet_history,
        "evm_alchemy_endpoint_pool_nontrace": (
            _trace._PREV_REFRESH_WALLET_HISTORY is _endpoint_pool.refresh_nontrace_with_endpoint_pool
        ),
        "evm_alchemy_endpoint_pool_progressive": (
            _trace._refresh_progressive is _endpoint_pool.refresh_progressive_with_endpoint_pool
        ),
        "evm_history_legacy_outer": _sibot._next_history_wallet is _legacy._next_history_wallet,
        "evm_history_legacy_to_context": _legacy._PREV_NEXT_HISTORY_WALLET is _context._next_history_wallet,
        "evm_history_context_to_trace": _context._PREV_NEXT_HISTORY_WALLET is _trace._next_history_wallet,
        "evm_history_trace_to_retry": _trace._PREV_NEXT_HISTORY_WALLET is _retry._next_history_wallet,
        "evm_history_background_worker_start": _sibot.start_workers is _legacy_drainer.start_workers_with_legacy_backlog_drainer,
        "evm_history_background_menu_start": _telegram_ui.start_menu_thread is _legacy_drainer.start_menu_thread_with_legacy_backlog_drainer,
        "evm_history_background_menu_inner": _legacy_drainer._PREV_START_MENU_THREAD is _friendly.start_menu_thread,
        "evm_history_background_menu_audit_inner": _friendly._PREV_START_MENU_THREAD is _audit_worker.start_menu_thread_with_transaction_audit,
        "evm_leader_cursor_ws_outer": _sibot.poll_leader_blocks is _evm_ws.poll_leader_blocks_locked,
        "evm_leader_cursor_reliable_inner": _evm_ws._ORIGINAL_POLL is _evm_reliability.poll_leader_blocks_reliable,
        "evm_quality_hard_floor": _sibot.user_settings is _evm_quality.user_settings_with_quality_floor,
        "evm_native_transfer_destination": _evm_live.LiveTrader.transfer_native is _evm_transfer.transfer_native_with_destination,
        "evm_pool_rug_manual_buy": _evm_live.LiveTrader.buy is _evm_rug.buy_with_pool_rug_gate,
        "evm_pool_rug_v2_prebroadcast": _evm_live.LiveTrader._prebroadcast_cycle is _evm_rug.prebroadcast_cycle_with_pool_rug_gate,
        "evm_pool_rug_v3_prebroadcast": _evm_live.LiveTrader._prebroadcast_v3_cycle is _evm_rug.prebroadcast_v3_cycle_with_pool_rug_gate,
        "poolcheck_evm_holder_concentration": (
            _evm_rug.evaluate_goplus is _poolcheck_hardening.evaluate_goplus_with_concentration
        ),
        "poolcheck_evm_activity_telemetry": (
            _evm_rug.evaluate_dexscreener is _poolcheck_hardening.evaluate_evm_dex_with_activity
        ),
        "poolcheck_evm_lp_concentration": (
            _evm_rug.external_pool_rug_check
            is _poolcheck_hardening.external_evm_pool_check_with_lp_concentration
        ),
        "poolcheck_evm_stress_exit": (
            _evm_rug._manual_roundtrip_check
            is _poolcheck_hardening.evm_roundtrip_with_stress_exit
        ),
        "poolcheck_solana_stress_exit": (
            _preflight._PREV_VALIDATE
            is _poolcheck_hardening.validate_solana_entry_with_stress_exit
        ),
        "poolcheck_solana_stress_cache_key": (
            _preflight._key is _poolcheck_hardening.solana_preflight_key_with_stress
        ),
        "evm_provenance_connect": _sibot.connect is _provenance._evm_connect_with_provenance,
        "solana_provenance_connect": _sol.connect is _provenance._sol_connect_with_provenance,
        "auto_provenance_append": _auto._append is _provenance._auto_append_with_provenance,
        "evm_live_audit_provenance": _evm_live.LiveTrader._audit is _provenance._live_audit_with_provenance,
        "basic_v0_auto_main": _auto.execute_best_live_opportunity is _basic_v0.execute_best_live_opportunity_v0,
        "basic_v0_fast_main": _basic_v0._fast.execute_best_live_opportunity is _basic_v0.execute_best_live_opportunity_v0,
        "basic_v0_cli_main": _basic_v0._cli.execute_best_live_opportunity is _basic_v0.execute_best_live_opportunity_v0,
    }


def install() -> None:
    checks = composition_checks()
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Final runtime integrity failed: " + ", ".join(failed))
    print("[final-runtime-integrity] OK audited_hooks=%d" % len(checks))


install()

from . import solana_operator_writeoff_8fip_migration as _writeoff_8fip  # noqa: E402
_writeoff_8fip.apply()
from . import solana_stuck_owner_warning_v2_patch  # noqa: E402,F401
from . import telegram_ai_target_score_patch  # noqa: E402,F401
