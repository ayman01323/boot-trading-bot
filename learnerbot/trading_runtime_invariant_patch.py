from __future__ import annotations

from . import sibot as _sibot
from . import sibot_evm_worker_reliability_patch as _evm_reliability
from . import solana_emergency_loss_halt_migration  # noqa: F401
from . import solana_entry_capacity_reconcile_patch as _capacity
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
from . import profit_control_audit_export_patch  # noqa: F401
from . import profit_control_master_summary_patch as _profit_master_summary


def install():
    checks = {
        "solana_close": _live._close_live is _exit_circuit.close_live_guarded,
        "solana_bound_close": _binding._close_bound_live is _exit_circuit.close_live_guarded,
        "solana_rent_close_inner": _rent._close_live_rent_aware is _exit_circuit._PREV_CLOSE,
        "solana_valuation": _sol.evaluate_position is _rent.evaluate_position_economic,
        "solana_monitor_positions": _sol.monitor_positions is _live.monitor_positions,
        "solana_leader_cursor": _sol.monitor_leaders is _cursor.monitor_leaders_reliable,
        "solana_workers": _sol.start_workers is _workers.start_workers_reliable,
        "solana_quote": _sol.jupiter_quote is _quote.jupiter_quote_executable,
        "solana_preflight": _sol._validate_shadow_entry is _preflight.validate_entry_cached,
        "solana_economic_gate": _live._economic_entry_gate is _overhead._economic_entry_gate_reconciled,
        "solana_capacity": _live._open_live_count is _capacity._verified_open_live_count,
        "solana_swap_validation": _exec.SolanaLiveExecutor.swap is _validation._swap_amounts_authoritative,
        "solana_simulation": _exec.SolanaLiveExecutor._simulate is _reserve._simulate_with_wallet_snapshot,
        "solana_buy": _exec.SolanaLiveExecutor.buy is _reserve._buy_with_simulated_reserve,
        "solana_profit_epoch": _profit_guard._copied_metrics is _epoch._copied_metrics_with_cleanup,
        "evm_leader_cursor": _sibot.poll_leader_blocks is _evm_reliability.poll_leader_blocks_reliable,
        "transaction_audit_worker": _telegram_ui.start_menu_thread is _audit_worker.start_menu_thread_with_transaction_audit,
        "profit_control_settings": _sol.settings is _profit_control.settings_with_profit_control,
        "profit_control_leader_gate": _profit_guard._copied_ok is _profit_control.copied_ok_with_profit_control,
        "profit_control_hourly_loop": _profit_master_summary._PREV_HOURLY_REVIEW is _profit_control.run_hourly_gpt_review_with_control,
        "profit_control_master_summary": _audit_worker.run_hourly_gpt_review is _profit_master_summary.run_hourly_review_with_master_control_summary,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Audited trading runtime invariant failed: " + ", ".join(failed))
    print("[trading-runtime-invariant] OK audited_hooks=%d" % len(checks))


install()
