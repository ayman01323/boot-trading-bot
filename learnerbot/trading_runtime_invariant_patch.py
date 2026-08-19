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
from . import solana_live_patch as _live
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


def _recompose_execution_validation():
    """Make final executor composition independent of earlier import-cache order.

    Some Telegram/runtime modules can import the validation patch before the later
    efficiency/atomic wrappers are installed.  The validation module deliberately
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
    # This is intentionally a repair-then-verify boundary.  It does not weaken an
    # invariant: it restores the one exact audited composition and then checks all
    # identities below.  Any later displacement still fails closed.
    _recompose_execution_validation()

    checks = {
        "solana_close": _live._close_live is _exit_circuit.close_live_guarded,
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
        "solana_monitor_reconciliation_outer": _sol.monitor_positions is _exit_circuit._monitor_with_exit_reconciliation,
        "solana_monitor_positions_inner": _exit_circuit._MONITOR_INNER is _live.monitor_positions,
        "solana_reconciliation_hook": _sol.reconcile_pending_exit_circuits is _exit_circuit.reconcile_pending_exit_circuits,
        "solana_leader_cursor": _sol.monitor_leaders is _cursor.monitor_leaders_reliable,
        "solana_workers": _sol.start_workers is _workers.start_workers_reliable,
        "solana_quote": _sol.jupiter_quote is _quote.jupiter_quote_executable,
        "solana_preflight": _sol._validate_shadow_entry is _preflight.validate_entry_cached,
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
        "profit_first_live_settings": _sol.settings is _profit_first_live.settings_profit_first_live,
        "profit_first_full_sell_inner": _partial_sell_guard._PREV_PROCESS is _profit_first_live.process_leader_event_profit_first,
        "partial_sell_guard_inner": _positive_edge._PREV_PROCESS is _partial_sell_guard.process_leader_event_partial_profit_guard,
        "partial_sell_hard_profit_floor": str(_partial_sell_guard._HARD_MIN_PARTIAL_NET_PCT) == "3.0",
        "partial_sell_low_capital_floor": str(_partial_sell_guard._HARD_MIN_POSITION_ECONOMIC_VALUE_SOL) == "0.002",
        "positive_edge_entry_gate": _sol.process_leader_event is _positive_edge.process_leader_event_positive_edge,
        "platform_amount_target": _positive_edge._HARD_PLATFORM_TARGET_PF >= _amount_objective.MIN_AMOUNT_PROFIT_FACTOR,
        "profit_control_leader_gate_inner": _positive_edge._PREV_COPIED_OK is _profit_control.copied_ok_with_profit_control,
        "positive_edge_first_loss_quarantine": _profit_guard._copied_ok is _positive_edge.copied_ok_quarantine_first_loss,
        "profit_control_hourly_loop": _profit_master_summary._PREV_HOURLY_REVIEW is _profit_control.run_hourly_gpt_review_with_control,
        "profit_control_master_summary": _audit_worker.run_hourly_gpt_review is _profit_master_summary.run_hourly_review_with_master_control_summary,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Audited trading runtime invariant failed: " + ", ".join(failed))
    print("[trading-runtime-invariant] OK audited_hooks=%d" % len(checks))


install()
