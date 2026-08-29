# Gpt engineering audit

{'highest_risk': 'EVM execution lacks atomic exit claiming, wallet-scoped nonce allocation, and durable ambiguous-broadcast reconciliation, permitting duplicate submissions and accounting divergence.', 'operational_efficiency_audit': {'api_model_cost': 'OPTIMISE: 47 paid-AI workflows, 38 @latest CLI installs, and expensive setup before material-change gates.', 'disk_usage': 'CRITICAL: sanitised snapshot reports 92.08% root usage and 573820928 free bytes; runner workspace is 319075967 bytes.', 'host_network': 'Host-wide, not bot-attributed: 180.566 MB/hour during the sampled interval. Bandwidth-plan limit is UNKNOWN.', 'server_bandwidth': 'OPTIMISE: 36 scheduled workflows, 11 scheduled self-hosted workflows, 22 full-history checkouts, 46 git fetches, 58 pip installs, and 23 npm installs.'}, 'result': 'Eight concrete defects and operational regressions were identified.', 'trade_latency': {'coverage': '0 of 0 seven-day trades; no per-trade evidence and no seven-day trade share.', 'current_24h': 'INSUFFICIENT DATA: count 0; p50 and p95 unavailable.', 'execution_success_slippage_pnl_relationship': 'UNKNOWN: telemetry contains no observed trades from which to establish a relationship.', 'high_resolution_solana_stages': {'coarse_event_receive_delay_ms': 'INSUFFICIENT DATA: 0 current and 0 baseline samples', 'execute_request_result_ms': 'INSUFFICIENT DATA: 0 current and 0 baseline samples', 'jupiter_order_ms': 'INSUFFICIENT DATA: 0 current and 0 baseline samples', 'post_execution_balance_ms': 'INSUFFICIENT DATA: 0 current and 0 baseline samples', 'simulation_ms': 'INSUFFICIENT DATA: 0 current and 0 baseline samples', 'strategy_preflight_ms': 'INSUFFICIENT DATA: 0 current and 0 baseline samples', 'total_event_to_result_ms': 'INSUFFICIENT DATA: 0 current and 0 baseline samples', 'total_local_execution_ms': 'INSUFFICIENT DATA: 0 current and 0 baseline samples', 'transaction_construction_signing_ms': 'INSUFFICIENT DATA: 0 current and 0 baseline samples'}, 'infrastructure_conclusion': 'BENCHMARK', 'infrastructure_reason': 'Current provider, region, monthly cost, alternative provider/region/cost, chain-weighted trade share, and same-workload candidate benefit are UNKNOWN. Zero high-resolution samples preclude MOVE.', 'observed_chains_with_trades_7d': [], 'preceding_six_day_same_server_baseline': 'INSUFFICIENT DATA: count 0; p50 and p95 unavailable.', 'rpc_round_trip': 'Separate getSlot probe: 3 samples, p50 203.22 ms, p95 222.46 ms, maximum 224.59 ms.', 'solana_metric': 'leader_signal_to_copy_entry_ms: leader-event timestamp to local LIVE copied-position entry; not validator confirmation latency.'}, 'verification': {'compileall': 'PASS', 'exact_commit_verified': True, 'pytest': 'INCOMPLETE: collection stopped with 43 errors because the supplied Python environment lacked PyYAML and solders.', 'worktree_clean': True}}

Status: ISSUES_FOUND

## P1 — Concurrent EVM exits can broadcast more than one sell
Two workers can sell the same OPEN position or submit transactions with colliding nonces, causing replacement, failure, unintended ordering, or excess disposal.

## P1 — Ambiguous broadcasts lose their transaction hash and can be retried
A transaction may later confirm on-chain while local state says rejected or open, allowing duplicate execution and corrupting exposure, fees, P&L, and notifications.

## P1 — Activation-code limits and user settings use unlocked read-modify-write operations
Concurrent callers can both redeem a single-use code or silently overwrite authorization and trading settings.

## P1 — Trade P&L attributes unrelated wallet balance changes to executions
Concurrent trades, transfers, rent changes, or other wallet activity can be misattributed to one trade, corrupting realised P&L, performance controls, and profit-share calculations.

## P2 — Profit-share submissions are accounted as paid without confirmation
Reported user net, master revenue, and outstanding liabilities can diverge from on-chain balances indefinitely.

## P2 — Read-only Strategy Lab reports mutate lifecycle state and grow decision history
Viewing a report changes control state, restarts can erase REWORK/REPLACE status and weaken the sizing throttle, and duplicate decisions grow SQLite storage.

## P2 — Scheduled AI adjudication performs expensive setup before material-change gates
Disabled or unchanged lanes repeatedly consume API/model budget, bandwidth, runner time, package caches, and workspace disk.

## P1 — Root filesystem has critically low free space without complete growth attribution
Package installation, checkout, SQLite WAL growth, backups, or deployment can exhaust the filesystem, causing database write failures, partial deployments, missing audit records, and service interruption.
