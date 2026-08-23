# GPT strategy review

Architecture-only review completed. The SHADOW framework correctly abstains when executable evidence is absent and prohibits promotion from quote/simulation scorecards. However, EVM cost accounting can materially misstate net edge: direct-market rows record zero gas, calculate slippage reserve from gross profit rather than notional, and may subtract separately estimated price impact from a size-specific executable quote that already embeds AMM impact. Solana leader observations intentionally lack a current executable-edge adapter and therefore remain ineligible. No profitability, CANARY-readiness, or LIVE-readiness conclusion is supportable. STRATEGY/MARKET losses must be measured from successfully executed positions with negative net outcomes; quote, simulation, RPC, signing, submission, inclusion, reconciliation, and exit failures must remain separately classified as EXECUTION/INFRASTRUCTURE failures.

## REWORK — Cross Venue Net Arbitrage
A positive executable edge must be computed with chain-specific EVM gas, builder payments, notional-based adverse-fill reserve, and failure/revert cost. Ambiguous or zero costs can rank economically negative routes as attractive, while double-counted impact can suppress genuinely positive routes.

## NEW_SHADOW — Liquidity Confirmed Momentum and Flow Acceleration
Historical leader profitability or flow acceleration is not current executable edge. A forward-only SHADOW adapter is needed to timestamp the decision, obtain contemporaneous Jupiter entry and exit quotes, estimate priority/base fees, price impact, adverse movement and route failure probability, and measure later net outcomes without look-ahead.

## IMPROVE — All SHADOW strategies
Strategy selection cannot be evaluated fairly if market losses and infrastructure failures are blended. Successful executions with negative net marked or realised outcomes are STRATEGY/MARKET losses. Quote, simulation, RPC, signing, submission, inclusion, reconciliation and forced-exit failures are EXECUTION/INFRASTRUCTURE failures, while their economic costs must still be charged to the deployable strategy.
