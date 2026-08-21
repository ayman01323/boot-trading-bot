# GPT strategy review

Architecture-only review completed. Both chains contain fail-closed simulation and positive-edge controls, but current evidence cannot establish profitability or CANARY/LIVE readiness. Solana cash-delta accounting addresses network fees and refundable rent, while EVM cycles reserve gas and reconcile cycle receipts. However, Strategy Lab cost fields do not explicitly cover gas, priority fees, price impact, failed-attempt costs, or profit-share settlement, and runtime forensics do not provide a consistent STRATEGY/MARKET versus EXECUTION/INFRASTRUCTURE attribution. These gaps can bias money-weighted net P&L and promotion decisions.

## IMPROVE — Strategy Lab evaluation and promotion accounting
Durable selection requires all-in money-weighted net P&L. Solana needs signature/priority fees, impact, irreversible account costs and failed-transaction fees separated from refundable rent. EVM needs actual gas, DEX fees/impact, failed or replaced transaction gas, and profit-share transfer costs.

## IMPROVE — Unified outcome and failure attribution
A landed, correctly executed trade losing from price movement is a STRATEGY/MARKET loss. Simulation, RPC, stale quote, revert, dropped transaction, invalid output, reconciliation, or fee-settlement problems are EXECUTION/INFRASTRUCTURE failures. Combining them obscures whether to change signals or execution.

## SHADOW_MORE — EVM atomic cycle arbitrage
The execution gate is conservative for cycle gas, but durable user net must also subtract profit share, its settlement gas, failed-attempt gas and any preparation/approval amortization. No fresh outcomes prove that quoted edge survives these costs and state latency.

## SHADOW_MORE — Solana leader-copy and market-native entries
Historical leader profitability and median return do not prove follower profitability after copy latency, impact, priority fees, failed exits and token-account rent treatment. The design is appropriately cautious, but runtime distributions are absent.
