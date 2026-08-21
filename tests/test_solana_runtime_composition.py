import subprocess
import sys


def test_final_runtime_hooks_match_audited_stack():
    # Import the execution stack in the same relevant order as learnerbot.__main__.
    # Later execution/accounting/liquidity/circuit protections remain authoritative,
    # while the final policy layer restores the first profitable Solana strategy
    # and adds a reject-only positive follower executable-edge preflight.
    script = r'''
from learnerbot import sibot as sibot
from learnerbot import solana_live_patch as live
from learnerbot import solana_execution_fault_counter_patch  # noqa
from learnerbot import solana_position_wallet_binding_patch as binding
from learnerbot import solana_profit_guard_patch as guard
from learnerbot import solana_overhead_gate_correction_patch as overhead
from learnerbot import solana_entry_capacity_reconcile_patch as capacity
from learnerbot import solana_quote_execution_consistency_patch as quote
from learnerbot import solana_preflight_cache_patch as preflight
from learnerbot import solana_profit_accounting_epoch_patch as epoch
from learnerbot import sibot_evm_worker_reliability_patch as evm
from learnerbot import solana_worker_reliability_patch as workers
from learnerbot import solana_leader_cursor_reliability_patch as cursor
from learnerbot import solana_token_account_reclaim_patch  # noqa
from learnerbot import solana_refundable_rent_accounting_patch as rent
from learnerbot import solana_simulated_reserve_guard_patch as reserve
from learnerbot import solana_execution_efficiency_patch as efficiency
from learnerbot import solana_atomic_close_fallback_patch as atomic_fallback
from learnerbot import solana_liquidity_fail_closed_patch as liquidity_guard
from learnerbot import solana_execution_validation_patch as validation
from learnerbot import solana_exit_circuit_breaker_patch as exit_circuit
from learnerbot import transaction_audit_worker_patch as audit_worker
from learnerbot import telegram_ui as telegram_ui
from learnerbot import trading_runtime_invariant_patch  # noqa
from learnerbot import profit_control_loop_patch as profit_control
from learnerbot import solana_profit_first_live_correction_patch as profit_first
from learnerbot import solana_partial_sell_profit_guard_patch as partial_guard
from learnerbot import solana_positive_edge_entry_gate_patch as positive_edge
from learnerbot import solana_first_day_strategy_restore_patch as first_day
from learnerbot import solana_leader_quality_restore_patch as quality_restore
from learnerbot import solana_live_executor as executor
from learnerbot import solana_sibot as sol

assert live._close_live is exit_circuit.close_live_guarded
assert binding._close_bound_live is exit_circuit.close_live_guarded
assert exit_circuit._PREV_CLOSE is efficiency.close_live_with_receipt_pnl
assert rent._close_live_rent_aware is efficiency.close_live_with_receipt_pnl
assert efficiency.execution_efficiency_stack_intact()
assert executor.SolanaLiveExecutor._order is efficiency.order_with_economic_caps
assert efficiency._validate_order is liquidity_guard.validate_order_fail_closed_on_unknown_liquidity
assert efficiency.sell_with_atomic_account_close is atomic_fallback.sell_with_atomic_or_capped_legacy_fallback
assert efficiency._build_atomic_swap is atomic_fallback.build_atomic_swap_excluding_rfq

assert executor.SolanaLiveExecutor.swap is validation._swap_amounts_authoritative
assert validation._PREV_SWAP is efficiency.swap_with_cost_receipt
assert executor.SolanaLiveExecutor.sell is validation._sell_with_token_reconciliation
assert validation._PREV_SELL is atomic_fallback.sell_with_atomic_or_capped_legacy_fallback
assert executor.SolanaLiveExecutor.buy is validation._buy_with_token_reconciliation
assert validation._PREV_BUY is reserve._buy_with_simulated_reserve
assert executor.SolanaLiveExecutor._simulate is reserve._simulate_with_wallet_snapshot

assert sol.evaluate_position is rent.evaluate_position_economic
assert sol.monitor_positions is exit_circuit._monitor_with_exit_reconciliation
assert exit_circuit._MONITOR_INNER is live.monitor_positions
assert sol.monitor_leaders is cursor.monitor_leaders_reliable
assert sol.start_workers is workers.start_workers_reliable
assert sol.jupiter_quote is quote.jupiter_quote_executable
assert sol._validate_shadow_entry is first_day.validate_entry_positive_executable_edge
assert first_day._PREV_VALIDATE is preflight.validate_entry_cached
assert live._economic_entry_gate is overhead._economic_entry_gate_reconciled
assert live._open_live_count is capacity._verified_open_live_count
assert guard._copied_metrics is epoch._copied_metrics_with_cleanup
assert sibot.poll_leader_blocks is evm.poll_leader_blocks_reliable
assert telegram_ui.start_menu_thread is audit_worker.start_menu_thread_with_transaction_audit

# The frequency/timing-only strategy modules are still importable/auditable and the
# owner-requested first-day *timing* profile is still in effect. Leader *quality* is
# no longer the first-day loosened profile: solana_leader_quality_restore_patch is
# the final policy layer and re-tightens win-rate/PF/history thresholds plus the
# leader-event circuit breaker back to the originally audited values.
assert profit_first._PREV_SETTINGS is profit_control.settings_with_profit_control
assert first_day.FIRST_DAY_REFERENCE_COMMIT == "f0ca88450fe96a316dc15e676fab1e36c1137285"
assert sol.settings is quality_restore.settings_quality_restored
assert quality_restore._PREV_SETTINGS is first_day.settings_first_day_strategy
assert sol.process_leader_event is positive_edge.process_leader_event_positive_edge
assert guard._copied_ok is positive_edge.copied_ok_quarantine_first_loss
assert sol.process_leader_event is not live.process_leader_event
assert sol.process_leader_event is not partial_guard.process_leader_event_partial_profit_guard
assert sol.process_leader_event is not profit_first.process_leader_event_profit_first
assert guard._copied_ok is not first_day.copied_ok_first_day

assert first_day.FIRST_DAY_STRATEGY_TARGETS["leaders_per_user"] == "5"
assert first_day.FIRST_DAY_STRATEGY_TARGETS["max_signal_age_seconds"] == "30"
assert first_day.FIRST_DAY_STRATEGY_TARGETS["max_roundtrip_loss_pct"] == "3"
assert first_day.FIRST_DAY_STRATEGY_TARGETS["max_entry_deterioration_pct"] == "2"
assert first_day.FIRST_DAY_STRATEGY_TARGETS["stop_loss_pct"] == "10"
assert first_day.FIRST_DAY_STRATEGY_TARGETS["take_profit_pct"] == "25"
assert first_day.FIRST_DAY_STRATEGY_TARGETS["live_require_positive_executable_edge"] == "true"
assert first_day.FIRST_DAY_STRATEGY_TARGETS["live_min_executable_net_edge_pct"] == "0.25"
print("AUDITED_TRADING_RUNTIME_COMPOSITION_OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "AUDITED_TRADING_RUNTIME_COMPOSITION_OK" in result.stdout
