# Gemini Frozen Snapshot Review

**Model:** `gemini-3.1-pro-preview`

## Status
COMPLETED

## Executive summary
The frozen snapshot demonstrates a highly defensive, safety-first architecture with robust circuit breakers, execution reconciliation, and strict economic profit controls (e.g., 1.30 Profit Factor requirement). However, the over-optimization for avoiding fixed costs has introduced structural weaknesses. Specifically, the 'Partial Sell Profit Guard' forces the bot to hold losing positions when a leader attempts to de-risk, exposing the portfolio to maximum stop-loss hits. Additionally, synchronous blocking HTTP calls to the Jupiter API with 30-second timeouts and severely restricted Jito tips (1000 lamports) will cause high-latency execution and dropped transactions during network congestion, directly degrading durable realized NET P&L.

## Strategy assessment
### Strategy — IMPROVE
Synchronous blocking calls introduce severe latency in fast-moving markets, leading to high slippage or missed execution windows. This degrades the expected edge calculated during preflight.
Evidence: solana_live_executor.py uses synchronous requests.get() with 30s timeouts for Jupiter /quote and /order endpoints.
Shadow test: Implement an asynchronous execution path using aiohttp/httpx to parallelize RPC and Jupiter API calls, measuring latency reduction and slippage improvement in SHADOW mode.

### Strategy — REWORK
While this prevents fixed fees from consuming small profits, it completely disables loss-mitigation. If a leader scales out of a bad trade to cut losses, the bot forces a HOLD, absorbing the full loss when the hard stop-loss is eventually hit.
Evidence: solana_partial_sell_profit_guard_patch.py rejects partial sells if net_pct < 3.0% or economic value < 0.002 SOL.
Shadow test: Modify the guard to allow partial sells at a loss IF the leader is actively de-risking, provided the recovered capital significantly exceeds the network fee.

### Strategy — KEEP
The graph-first approach is highly efficient and safe, ensuring only liquid, existing pairs are quoted. The strict slippage and price impact checks protect capital.
Evidence: market_scanner.py validates factory pairs locally before querying the router, avoiding blind RPC spam.
Shadow test: None required; continue monitoring existing metrics.

### Strategy — KEEP
Optimizing for gross profit amount vs gross loss amount is mathematically superior to optimizing for win count, ensuring durable net profitability.
Evidence: profit_control_amount_objective_patch.py enforces a strict 1.30 Profit Factor (amount-based) rather than just win rate.
Shadow test: None required; the logic is sound.

## Recommended next action
RUN_SHADOW_EXPERIMENTS

## Must not change
- solana_execution_validation_patch.py: The requirement to reconcile on-chain transaction metadata before retrying ambiguous Jupiter successes.
- profit_control_amount_objective_patch.py: The 1.30 minimum Profit Factor requirement for strategy success.
- solana_position_wallet_binding_patch.py: The strict binding of a position to its entry wallet to prevent cross-wallet contamination.
- solana_exit_circuit_breaker_patch.py: The quarantine mechanism for landed-invalid or reconciling exits.
