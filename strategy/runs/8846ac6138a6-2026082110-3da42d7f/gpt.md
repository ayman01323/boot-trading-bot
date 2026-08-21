# GPT strategy review

Architecture-only review completed. The repository contains strong fail-closed quoting, simulation, liquidity, sellability and positive-edge controls, but current evaluation can overstate durable money-weighted net performance: Strategy Lab can aggregate incomparable chain-native amounts, omits explicit gas, price-impact and failure-cost fields, and does not distinguish STRATEGY/MARKET losses from EXECUTION/INFRASTRUCTURE failures. EVM failed broadcasts can also omit consumed gas from realised P&L. Solana shadow accounting relies partly on fixed fee estimates rather than observed priority-fee distributions. Runtime forensics are unavailable, so no profitability, CANARY-readiness or LIVE-readiness claim is made.

## REWORK — Strategy Lab lifecycle evaluation
Promotion evidence must remain chain and currency coherent and include every executable cost. Otherwise SOL, ETH and other native amounts, or differently sized trades, can produce misleading aggregate results.

## IMPROVE — Execution-aware strategy attribution
A negative executable signal is a STRATEGY/MARKET loss. RPC failure, stale nonce, reverted transaction, receipt timeout or quote/simulation infrastructure failure is EXECUTION/INFRASTRUCTURE failure. Both affect retained net economics, but require different remediation and must not be conflated.

## SHADOW_MORE — Solana leader-copy and market-entry strategies
Solana profitability is sensitive to time-varying priority fees, route impact, account creation/rent exposure and failed-transaction fees. Fixed shadow fee assumptions may not reproduce executable net economics during congestion.

## KEEP — EVM atomic cyclic arbitrage
The execution architecture appropriately requires fresh, wallet-specific, atomic executable edge and does not force trades. It should remain unchanged pending fresh outcome evidence, subject to failure-cost accounting improvements.
