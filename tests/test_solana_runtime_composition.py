import subprocess
import sys


def test_final_runtime_hooks_match_audited_stack():
    # Import the execution stack in the same relevant order as learnerbot.__main__:
    # wallet binding -> rent tracking/accounting -> signed reserve guard -> economic
    # efficiency -> capped legacy fallback -> authoritative validation -> exit circuit.
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
from learnerbot import solana_execution_validation_patch as validation
from learnerbot import solana_exit_circuit_breaker_patch as exit_circuit
from learnerbot import transaction_audit_worker_patch as audit_worker
from learnerbot import telegram_ui as telegram_ui
from learnerbot import trading_runtime_invariant_patch  # noqa
from learnerbot import solana_live_executor as executor
from learnerbot import solana_sibot as sol

assert live._close_live is exit_circuit.close_live_guarded
assert binding._close_bound_live is exit_circuit.close_live_guarded
assert exit_circuit._PREV_CLOSE is efficiency.close_live_with_receipt_pnl
assert rent._close_live_rent_aware is efficiency.close_live_with_receipt_pnl
assert efficiency.execution_efficiency_stack_intact()
assert executor.SolanaLiveExecutor._order is efficiency.order_with_economic_caps
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
assert sol.monitor_positions is live.monitor_positions
assert sol.monitor_leaders is cursor.monitor_leaders_reliable
assert sol.start_workers is workers.start_workers_reliable
assert sol.jupiter_quote is quote.jupiter_quote_executable
assert sol._validate_shadow_entry is preflight.validate_entry_cached
assert live._economic_entry_gate is overhead._economic_entry_gate_reconciled
assert live._open_live_count is capacity._verified_open_live_count
assert guard._copied_metrics is epoch._copied_metrics_with_cleanup
assert sibot.poll_leader_blocks is evm.poll_leader_blocks_reliable
assert telegram_ui.start_menu_thread is audit_worker.start_menu_thread_with_transaction_audit
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
