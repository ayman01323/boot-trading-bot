# GPT strategy review

Architecture-only review completed and selected tests passed. Execution protections generally fail closed and use chain-specific controls, but promotion accounting is not yet sufficient for durable money-weighted NET P&L. Strategy Lab permits caller-supplied net profit, calculates profit factor from pre-cost gains/losses, and counts execution failures without their economic cost or a durable STRATEGY/MARKET versus EXECUTION/INFRASTRUCTURE classification. No profitability, CANARY-readiness, or LIVE-readiness conclusion is possible without fresh runtime forensics.

## REWORK — Strategy Lab promotion accounting
A negative completed trade after correct execution is a STRATEGY/MARKET loss. Reverts, timeouts, stale quotes, RPC/build failures, invalid landed output, and failed exits are EXECUTION/INFRASTRUCTURE failures; their paid gas, priority fees, tips, and opportunity/exit costs still reduce NET P&L. Promotion decisions must preserve this distinction while charging both categories to economic performance.

## SHADOW_MORE — Solana leader-copy
The controls address Solana-specific execution economics, but leader-copy latency and exit blockage can turn leader alpha into follower STRATEGY/MARKET loss. RPC, Jupiter, congestion, simulation, landed-invalid-output, and account-close problems must be separately measured as EXECUTION/INFRASTRUCTURE failures.

## SHADOW_MORE — EVM atomic cycle arbitrage
Atomicity limits market exposure, but EVM profitability remains sensitive to base-fee movement, priority bidding, state contention, reverts, nonce/replacement behavior, approvals, and fee-settlement transactions. A valid reverted transaction is an EXECUTION/INFRASTRUCTURE loss, while an atomically successful but economically negative receipt is a STRATEGY/MARKET/modeling loss.
