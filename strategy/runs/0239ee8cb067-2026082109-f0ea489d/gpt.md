# GPT strategy review

Architecture-only review completed. The repository generally fails closed, separates SHADOW from signing, and evaluates money-weighted results rather than raw win count. However, current Strategy Lab accounting can accept externally supplied net P&L without independently itemising every chain-specific cost, SHADOW quote results lack delayed realised counterfactual outcomes, and some EVM SiBot exceptions are silently discarded. These gaps can confound STRATEGY/MARKET loss with EXECUTION/INFRASTRUCTURE failure. No profitability, CANARY-readiness, or LIVE-readiness conclusion is supported without fresh runtime forensics.

## IMPROVE — Strategy Lab evaluation and all registered strategy families
Promotion decisions should use a reproducible money-weighted identity whose cost components differ by chain. Solana needs base/priority fees, ATA/rent treatment, quote impact, failed transaction fees and unwind costs; EVM needs swap gas, approvals, EIP-1559 priority/base fees, builder payments, taxes and reverted-attempt gas. Independently classify negative market outcomes as STRATEGY/MARKET and unsuccessful or degraded execution as EXECUTION/INFRASTRUCTURE.

## SHADOW_MORE — Cross Venue Net Arbitrage, Liquidity Confirmed Momentum, Dislocation Mean Reversion, Flow Acceleration and other SHADOW families
Contemporaneous executable quotes test entry feasibility, not durable strategy edge. Each signal needs delayed, non-overlapping, out-of-sample entry and exit marks using executable size, actual route constraints and chain-specific failure/cost assumptions.

## REWORK — SiBot leader-copy outcome attribution
A strategy that identified positive executable edge but failed because of RPC, stale blockhash, nonce, allowance, simulation, rate limit or settlement issues must not be diagnosed as a market-loss strategy. Conversely, successful execution followed by adverse price movement belongs to STRATEGY/MARKET.
