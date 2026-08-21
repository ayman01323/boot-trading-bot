# GPT strategy review

Architecture-only review completed. The repository contains strong fail-closed quote, simulation, liquidity, sellability and reconciliation protections, but current Strategy Lab and canary accounting cannot yet establish durable money-weighted net profitability. In particular, EVM records can omit platform-fee settlement costs and treat BROADCAST/expected results as outcomes, while lifecycle aggregation mixes native-token amounts without notional-normalised returns. Solana Strategy Lab correctly lacks current executable-edge evidence and must remain SHADOW/DORMANT. STRATEGY/MARKET losses must be measured from confirmed, reconciled executions or fixed-horizon executable counterfactuals; RPC, simulation, broadcast, landing, receipt and reconciliation faults must remain separate EXECUTION/INFRASTRUCTURE failures. No profitability, CANARY-readiness or LIVE-readiness claim is supported.

## SHADOW_MORE — Cross-chain Strategy Canary lifecycle
A few positive trades or a large winner can promote a strategy despite weak money-weighted evidence, chain-specific cost differences or concentrated regime exposure. Solana and EVM native amounts are not economically comparable.

## IMPROVE — Direct Market Arbitrage and Leader Copy measurement
Expected or merely broadcast results are not realised P&L. Collapsing costs into net prevents diagnosis of STRATEGY/MARKET loss versus EXECUTION/INFRASTRUCTURE failure and can overstate end-user profitability.

## NEW_SHADOW — Solana Leader Copy executable-edge validation
Historical leader profitability and a successful buy simulation do not prove that a delayed follower can exit profitably after Jupiter routing, price impact, priority fees, account rent and leader-copy latency.
