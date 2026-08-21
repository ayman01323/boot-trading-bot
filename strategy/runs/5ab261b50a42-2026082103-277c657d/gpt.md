# GPT strategy review

Architecture-only review completed. The repository contains strong fail-closed quote, liquidity, sellability, simulation and non-promotion safeguards, but no fresh runtime forensics supports profitability or promotion. Solana SHADOW features deliberately lack current executable edge, while Strategy Lab aggregation does not fully attribute failed-execution costs or distinguish STRATEGY/MARKET losses from EXECUTION/INFRASTRUCTURE failures. Preserve all live protections and improve only SHADOW measurement and attribution.

## IMPROVE — Strategy Lab portfolio evaluation
Add a SHADOW-only all-in ledger that attributes each opportunity to STRATEGY/MARKET loss, EXECUTION failure, or INFRASTRUCTURE failure. Strategy/market loss means a successfully executed position lost after every cost. Execution failure includes revert, expired quote, adverse-fill rejection or on-chain failure and must include any consumed fees. Infrastructure failure includes RPC, indexing, worker or receipt-reconciliation faults and must not be presented as market loss. Compute money-weighted net P&L and profit factor from realised all-in outcomes, including failed-attempt gas or priority fees.

## NEW_SHADOW — Solana current executable-edge adapter
Historical leader returns cannot establish follower edge under current Solana liquidity, price impact, latency and priority-fee conditions. Add only a non-signing SHADOW adapter based on contemporaneous round-trip executable quotes and simulation, including base fee, priority/Jito tip, platform fee, slippage, route price impact, latency deterioration and non-refundable account costs. Keep refundable rent as capital exposure rather than permanent strategy loss, then reconcile its eventual recovery separately.

## SHADOW_MORE — EVM cross-venue and learned-route strategies
Retain the EVM admission gates, but calibrate simulated edge against delayed executable re-quotes and sanitised realised receipts. Separate market decay or adverse price movement from reverts, nonce/allowance failures and RPC/worker failures. Include gas paid by failed transactions and opportunity loss from transactions that never landed.

## KEEP — Cross-chain executable-edge gating
These are appropriate architectural protections against manufacturing trades without positive executable edge. Chain-specific calibration is still required before their numeric thresholds can be validated.
