from __future__ import annotations

from . import sibot as _sibot
from . import sibot_evm_worker_reliability_patch as _evm_reliability
from . import solana_emergency_loss_halt_migration  # noqa: F401
from . import solana_entry_capacity_reconcile_patch as _capacity
from . import solana_execution_efficiency_patch as _efficiency
from . import solana_atomic_close_fallback_patch as _atomic_fallback
from . import solana_liquidity_fail_closed_patch as _liquidity_guard
from . import solana_execution_validation_patch as _validation
from . import solana_exit_circuit_breaker_patch as _exit_circuit
from . import solana_leader_cursor_reliability_patch as _cursor
from . import solana_live_executor as _exec
from . import solana_overhead_gate_correction_patch as _overhead
from . import solana_position_wallet_binding_patch as _binding
from . import solana_preflight_cache_patch as _preflight
from . import solana_profit_accounting_epoch_patch as _epoch
from . import solana_profit_guard_patch as _profit_guard
from . import solana_quote_execution_consistency_patch as _quote
from . import solana_refundable_rent_accounting_patch as _rent
from . import solana_simulated_reserve_guard_patch as _reserve
from . import solana_sibot as _sol
from . import solana_worker_reliability_patch as _workers
from . import telegram_ui as _telegram_ui
from . import transaction_audit_worker_patch as _audit_worker
from . import hourly_gpt_live_engine_wording_patch  # noqa: F401
from . import profit_control_loop_patch as _profit_control
from . import profit_control_amount_objective_patch as _amount_objective
from . import profit_control_audit_export_patch  # noqa: F401
from . import profit_control_master_summary_patch as _profit_master_summary
from . import solana_profit_first_live_correction_patch as _profit_first_live
from . import solana_partial_sell_profit_guard_patch as _partial_sell_guard
from . import solana_positive_edge_entry_gate_patch as _positive_edge
# Owner-requested strategy rollback is deliberately imported AFTER the later
# strategy wrappers so it can restore first-day entry/selection policy behaviour
# while leaving all execution/accounting/simulation/liquidity/circuit safety
# wrappers intact. The easy-exit overlay below independently controls exits.
from . import solana_first_day_strategy_restore_patch as _first_day_strategy
# Final leader-quality layer: re-tighten Solana leader-quality thresholds and
# restore the leader-event circuit breaker while keeping first-day opportunity
# frequency/timing and the positive-executable-edge preflight intact.
from . import solana_leader_quality_restore_patch as _quality_restore
# Observational layer over open-position monitoring: a read-only liquidity re-quote
# that can only notify, never close/resize/execute.
from . import solana_position_liquidity_health_patch as _liquidity_health
# Final EXIT-only overlay. It restores the earlier easier stop/profit thresholds and
# retries already-requested blocked full leader exits without altering BUY policy.
from . import solana_easy_exit_policy_patch as _easy_exit
# Risk-reducing exit priority loaded by the safe-slice startup hook. It must remain
# outside the audited exit-circuit close while preserving that circuit as its inner.
from . import solana_rpc_exit_priority_patch as _rpc_exit_priority
# Final downside-only overlay loaded by the safe-slice startup hook. It wraps the
# easy-exit monitor/settings without weakening the existing execution guards.
from . import solana_loss_containment_patch as _loss_containment


def _recompose_execution_validation():
    """Make final executor composition independent of earlier import-cache order.

    Some Telegram/runtime modules can import the validation patch before the later
    efficiency/atomic wrappers are installed. The validation module deliberately
    has an idempotency flag, so a later normal import will not re-run install().
    At the final audited runtime boundary we know the exact intended inner stack;
    re-bind those captured inner functions and then restore validation as the
    authoritative outer BUY/SELL/swap layer before verifying the invariant.
    """
    _validation._PREV_SWAP = _efficiency.swap_with_cost_receipt
    _validation._PREV_SELL = _atomic_fallback.sell_with_atomic_or_capped_legacy_fallback
    _validation._PREV_BUY = _reserve._buy_with_simulated_reserve
    _exec.SolanaLiveExecutor.swap = _validation._swap_amounts_authoritative
    _exec.SolanaLiveExecutor.sell = _validation._sell_with_token_reconciliation
    _exec.SolanaLiveExecutor.buy = _validation._buy_with_token_reconciliation
    _exec.SolanaLiveExecutor._economic_validation_patch = True


def install():
    # This is intentionally a repair-then-verify boundary. It does not weaken an
    # invariant: it restores the one exact audited execution composition and then
    # checks all identities below. Any later displacement still fails closed.
    _recompose_execution_validation()

    checks = {
        # RPC-priority is now the intended outer close wrapper. The previously
        # audited exit circuit must remain immediately inside it.
        "solana_close_rpc_priority_outer": _live._close_live is _rpc_exit_priority.close_live_with_rpc_priority,
        "solana_close_exit_circuit_inner": _rpc_exit_priority._PREV_CLOSE_LIVE is _exit_circuit.close_live_guarded,
        "solana_rpc_priority_active": _sol._rpc is _rpc_exit_priority.rpc_with_exit_priority,
        "solana_bound_close": _binding._close_bound_live is _exit_circuit.close_live_guarded,
        "solana_exit_inner_efficiency": _exit_circuit._PREV_CLOSE is _efficiency.close_live_with_receipt_pnl,
        "solana_rent_close_efficiency": _rent._close_live_rent_aware is _efficiency.close_live_with_receipt_pnl,
        "solana_execution_efficiency_stack": _efficiency.execution_efficiency_stack_intact(),
        "solana_order_fee_guard": _exec.SolanaLiveExecutor._order is _efficiency.order_with_economic_caps,
        "solana_liquidity_fail_closed": _efficiency._validate_order is _liquidity_guard.validate_order_fail_closed_on_unknown_liquidity,
        "solana_atomic_legacy_fallback": _efficiency.sell_with_atomic_account_close is _atomic_fallback.sell_with_atomic_or_capped_legacy_fallback,
        "solana_atomic_build_rfq_excluded": _efficiency._build_atomic_swap is _atomic_fallback.build_atomic_swap_excluding_rfq,
        "solana_swap_validation": _exec.SolanaLiveExecutor.swap is _validation._swap_amounts_authoritative,
        "solana_swap_efficiency_inner": _validation._PREV_SWAP is _efficiency.swap_with_cost_receipt,
        "solana_sell_validation": _exec.SolanaLiveExecutor.sell is _validation._sell_with_token_reconciliation,
        "solana_sell_atomic_inner": _validation._PREV_SELL is _atomic_fallback.sell_with_atomic_or_capped_legacy_fallback,
        "solana_buy_validation": _exec.SolanaLiveExecutor.buy is _validation._buy_with_token_reconciliation,
        "solana_buy_reserve_inner": _validation._PREV_BUY is _reserve._buy_with_simulated_reserve,
        "solana_simulation": _exec.SolanaLiveExecutor._simulate is _reserve._simulate_with_wallet_snapshot,
        "solana_valuation": _sol.evaluate_position is _rent.evaluate_position_economic,

        # Loss containment is the intended outer monitor. Its inner stack remains
        # easy-exit -> liquidity-health -> exit-reconciliation -> base monitor.
        "solana_monitor_loss_containment_outer": _sol.monitor_positions is _loss_containment.monitor_positions_loss_containment,
        "solana_monitor_easy_exit_outer": _loss_containment._PREV_MONITOR_POSITIONS is _easy_exit.monitor_positions_easy_exit,
        "solana_monitor_easy_exit_inner": _easy_exit._PREV_MONITOR_POSITIONS is _liquidity_health.monitor_positions_with_liquidity_health,
        "solana_monitor_reconciliation_inner": _liquidity_health._PREV_MONITOR_POSITIONS is _exit_circuit._monitor_with_exit_reconciliation,
        "solana_monitor_positions_inner": _exit_circuit._MONITOR_INNER is _live.monitor_positions,
        "solana_reconciliation_hook": _sol.reconcile_pending_exit_circuits is _exit_circuit.reconcile_pending_exit_circuits,
        "solana_leader_cursor": _sol.monitor_leaders is _cursor.monitor_leaders_reliable,
        "solana_workers": _sol.start_workers is _workers.start_workers_reliable,
        "solana_quote": _sol.jupiter_quote is _quote.jupiter_quote_executable,
        "solana_positive_executable_edge": _sol._validate_shadow_entry is _first_day_strategy.validate_entry_positive_executable_edge,
        "solana_preflight_cache_inner": _first_day_strategy._PREV_VALIDATE is _preflight.validate_entry_cached,
        "solana_economic_gate": _live._economic_entry_gate is _overhead._economic_entry_gate_reconciled,
        "solana_capacity": _live._open_live_count is _capacity._verified_open_live_count,
        "solana_profit_epoch": _profit_guard._copied_metrics is _epoch._copied_metrics_with_cleanup,
        "evm_leader_cursor": _sibot.poll_leader_blocks is _evm_reliability.poll_leader_blocks_reliable,
        "transaction_audit_worker": _telegram_ui.start_menu_thread is _audit_worker.start_menu_thread_with_transaction_audit,

        "profit_control_amount_objective": (
            _profit_control._is_success is _amount_objective.is_success_amount_first
            and _profit_control.MIN_SUCCESS_PROFIT_FACTOR == _amount_objective.MIN_AMOUNT_PROFIT_FACTOR
        ),
        "profit_control_settings_inner": _profit_first_live._PREV_SETTINGS is _profit_control.settings_with_profit_control,
        "first_day_strategy_reference": _first_day_strategy.FIRST_DAY_REFERENCE_COMMIT == "f0ca88450fe96a316dc15e676fab1e36c1137285",

        # Settings now compose loss-containment -> easy-exit -> quality-restore ->
        # first-day. This preserves the 5% downside cap while allowing winners to run.
        "loss_containment_settings_active": _sol.settings is _loss_containment.settings_loss_containment,
        "easy_exit_settings_active": _loss_containment._PREV_SETTINGS is _easy_exit.settings_easy_exit,
        "easy_exit_settings_inner": _easy_exit._PREV_SETTINGS is _quality_restore.settings_quality_restored,
        "leader_quality_settings_inner": _quality_restore._PREV_SETTINGS is _first_day_strategy.settings_first_day_strategy,
        "leader_quality_process_restored": _sol.process_leader_event is _positive_edge.process_leader_event_positive_edge,
        "leader_full_sell_immediate_inner": _partial_sell_guard._PREV_PROCESS is _profit_first_live.process_leader_event_profit_first,
        "leader_partial_sell_profit_guard_inner": _positive_edge._PREV_PROCESS is _partial_sell_guard.process_leader_event_partial_profit_guard,
        "leader_quality_copied_guard_restored": _profit_guard._copied_ok is _positive_edge.copied_ok_quarantine_first_loss,
        "first_day_signal_age": _first_day_strategy.FIRST_DAY_STRATEGY_TARGETS.get("max_signal_age_seconds") == "30",
        "easy_exit_stop_loss": _easy_exit.EASY_EXIT_LIMITS.get("stop_loss_pct") == "5",
        "easy_exit_take_profit": _easy_exit.EASY_EXIT_LIMITS.get("take_profit_pct") == "10",
        "easy_exit_break_even": _easy_exit.EASY_EXIT_LIMITS.get("break_even_trigger_pct") == "3",
        "easy_exit_trailing": _easy_exit.EASY_EXIT_LIMITS.get("trailing_trigger_pct") == "5",
        "easy_exit_leader_pending_any_pnl": _easy_exit._PENDING_REASON in _easy_exit._emergency._LOSS_EXIT_REASONS,
        "loss_containment_stop_max_5": _loss_containment._HARD_EXIT_REASON in _loss_containment._emergency._LOSS_EXIT_REASONS,
        "loss_containment_winner_tp_min_100": _sol.DEFAULTS.get("loss_containment_winner_take_profit_pct", ("",))[0] == "100",
        "loss_containment_break_even_min_10": _sol.DEFAULTS.get("loss_containment_break_even_trigger_pct", ("",))[0] == "10",
        "loss_containment_trailing_min_20": _sol.DEFAULTS.get("loss_containment_trailing_trigger_pct", ("",))[0] == "20",
        "first_day_positive_edge_required": _first_day_strategy.FIRST_DAY_STRATEGY_TARGETS.get("live_require_positive_executable_edge") == "true",
        "first_day_positive_edge_floor": _first_day_strategy.FIRST_DAY_STRATEGY_TARGETS.get("live_min_executable_net_edge_pct") == "0.25",
        "profit_control_hourly_loop": _profit_master_summary._PREV_HOURLY_REVIEW is _profit_control.run_hourly_gpt_review_with_control,
        "profit_control_master_summary": _audit_worker.run_hourly_gpt_review is _profit_master_summary.run_hourly_review_with_master_control_summary,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Audited trading runtime invariant failed: " + ", ".join(failed))
    print("[trading-runtime-invariant] OK audited_hooks=%d" % len(checks))


install()
