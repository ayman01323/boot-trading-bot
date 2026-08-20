# GPT strategy review

Architecture-only review completed. The repository generally fails closed, requires positive estimated net edge, preserves sellability/liquidity/simulation controls, and avoids optimizing raw win count. However, current SHADOW accounting is not sufficient to establish durable money-weighted net P&L: Solana has no current executable-edge adapter, EVM simulated outcomes are not realised outcomes, execution failures are not economically charged to strategy results, and Strategy Lab promotion thresholds do not require chain-specific cost completeness or bound execution-failure rates. STRATEGY/MARKET losses must be measured from successfully executed positions after all costs; EXECUTION/INFRASTRUCTURE failures must be reported separately while also charging any incurred gas, priority fees, rent, or failed-transaction costs to net P&L. No profitability, CANARY-readiness, or LIVE-readiness conclusion is supported.

## IMPROVE — Strategy Lab portfolio evaluation
A strategy can appear positive when omitted chain costs or failed attempts are material. Preserve separate cause labels: completed-trade adverse movement is STRATEGY/MARKET loss; RPC, simulation, broadcast, revert, expiry, or confirmation failure is EXECUTION/INFRASTRUCTURE failure. Any fee actually paid by the latter must still reduce money-weighted net P&L.

## SHADOW_MORE — Solana strategy families
Leader history or positive-trade ratio is not a contemporaneous executable edge. Solana needs decision-time round-trip quotes and chain-specific costs, including priority/Jito fees, price impact, token-account rent effects, quote expiry, blockhash expiry, and failed-signature fees.

## SHADOW_MORE — EVM Cross Venue Net Arbitrage and Learned Route Replication
The EVM execution gate is conservative, but quote/simulation success cannot establish durable net profitability under state movement, inclusion latency, reverts, replacements, and dynamic base/priority fees.
