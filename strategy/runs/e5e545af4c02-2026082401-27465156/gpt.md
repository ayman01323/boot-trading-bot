# GPT strategy review

Architecture-only review completed. The repository has fail-closed execution gates and separates SHADOW research from LIVE promotion, but two research-accounting weaknesses could overstate expected edge: learned-route averages exclude proven losing outcomes, and SHADOW quote results reuse decision-time expected edge rather than independently measured future outcomes. Solana is currently fail-closed because its Strategy Lab adapter lacks contemporaneous executable quote economics. No profitability, CANARY-readiness, or LIVE-readiness conclusion is possible without fresh realised, chain-attributed runtime forensics. Future analysis must classify negative outcomes as STRATEGY/MARKET loss when valid execution completed but the market outcome was adverse, versus EXECUTION/INFRASTRUCTURE failure when quote expiry, RPC, simulation, submission, inclusion, reconciliation, or exit failure caused the loss or prevented execution.

## REWORK — Learned Route Replication
A positive-only historical mean is selection-biased and can make a route with large or frequent losses appear attractive. Route evidence should include every proven closed outcome, money-weighted by deployed notional, with gas, builder fees, slippage, price impact, failed-execution costs, and platform fees attributed by EVM chain.

## IMPROVE — Cross-chain Strategy Lab scorecards
Decision-time quote economics validate entry gating but cannot demonstrate realised expectancy. A later, immutable counterfactual outcome should re-quote the intended exit and record all costs, while separately labelling adverse market movement and technical execution failure.

## NEW_SHADOW — Solana leader-following executable-edge filter
Solana cannot be evaluated by the common Strategy Lab until contemporaneous entry and exit quote economics exist. The adapter should remain SHADOW-only and include Jupiter route price impact, slippage, priority/base fees, refundable versus non-refundable rent, sellability, and execution-failure reserves.
