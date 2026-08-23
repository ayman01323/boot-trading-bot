# Gpt engineering audit

{'highest_risk': 'EVM execution lacks durable transaction claiming and ambiguous-broadcast reconciliation, permitting duplicate submissions and incorrect accounting.', 'operational_efficiency': {'api_model_cost': 'OPTIMISE', 'disk_usage': 'WATCH: root filesystem 84.54% used, 2,157,146,112 bytes free; runner workspace 215,867,914 bytes.', 'host_network': 'Host-wide only: approximately 968.92 MB/hour in the latest interval; it cannot be attributed solely to the bot.', 'server_bandwidth': 'OPTIMISE'}, 'result': 'Six concrete defects or operational regressions were identified.', 'trade_latency': {'current_24h': 'INSUFFICIENT DATA: 0 samples', 'execution_outcome_relationship': 'UNKNOWN: no observed trades support a latency relationship with success, slippage, or realised P&L.', 'high_resolution_execution_stages': 'INSUFFICIENT DATA: zero samples for coarse receive delay, strategy/preflight, Jupiter order, transaction construction/signing, simulation, execute, post-balance, local total, and event-to-result total.', 'infrastructure_conclusion': 'BENCHMARK', 'infrastructure_reason': 'Current provider, region, monthly cost, candidate prices, measured candidate benefit, and chain-weighted trade share are UNKNOWN. No MOVE case is supported.', 'observed_chains_with_trades_7d': [], 'preceding_six_day_baseline': 'INSUFFICIENT DATA: 0 samples', 'rpc_round_trip': '3 samples; p50 235.63 ms, p95 568.84 ms, max 605.87 ms', 'solana_metric': 'leader_signal_to_copy_entry_ms; observed leader-event timestamp to local LIVE copied-position entry, not validator confirmation latency'}, 'verification': {'checkout_exact': True, 'pytest': 'INCOMPLETE: collection stopped with 43 errors because the supplied environment lacked PyYAML and solders.', 'python_compile': 'PASS', 'worktree_clean_before_audit': True}}

Status: ISSUES_FOUND

## P1 — EVM position exits can be submitted more than once
Concurrent monitor/event paths can both observe OPEN, build transactions from the same balance, and broadcast competing or sequential SELLs. Shared-wallet operations can also reuse a nonce, causing replacement, failure, or unintended ordering.

## P1 — Ambiguous EVM broadcasts are recorded as rejected and may be retried
A transaction accepted by the node but not confirmed within the local timeout can execute on-chain while local state says REJECTED or OPEN. Subsequent cycles can repeat the trade, and realised P&L, exposure, fees, and notifications become incorrect.

## P1 — Activation-code use limits are not atomic
Two users can redeem a nominally single-use activation code concurrently and both become active. Concurrent user or trading-setting writes can also silently overwrite one another.

## P2 — Profit-share liabilities are booked as paid without confirmation or recovery
Reported user net, master fee revenue, and fee liabilities diverge from on-chain balances when a fee transaction is replaced, reverted, dropped, or never sent. Pending fees can remain permanently unsettled.

## P2 — Read-only Strategy Lab reports mutate lifecycle state and grow the database
Opening a report changes strategy lifecycle state and adds duplicate decisions. Process restarts or first events can increment versions and erase REPLACE/REWORK/PROMOTION_CANDIDATE status back to SHADOW, undermining the live sizing throttle and growing SQLite indefinitely.

## P2 — High-frequency adjudication installs every provider CLI before determining that no work is needed
The selected-master schedule can create up to 576 matrix jobs per day that download dependencies and full history even when disabled or already adjudicated. This wastes GitHub/network resources and increases cache/worktree pressure on an already constrained VPS ecosystem.
