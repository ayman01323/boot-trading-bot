# GPT strategy review

Architecture-only review completed. The repository generally fails closed on absent executable edge and contains chain-specific protections, but no fresh runtime evidence supports profitability, CANARY readiness or LIVE readiness. Solana Strategy Lab inputs deliberately lack current executable economics, while EVM opportunities require exact quote, simulation, liquidity, sellability and whole-route approval before simulated outcomes are recorded. Strategy/market losses must be measured separately from execution/infrastructure failures; the current canary state separates an execution-failure count but the SHADOW scorecard lacks a complete failure taxonomy and money-weighted realised outcome model.

## NEW_SHADOW — Solana executable-edge observation
Without a time-aligned entry quote, executable exit quote and Solana-specific fee/rent assumptions, leader-copy signals cannot be evaluated for follower net edge. Historical leader win ratios are not executable edge.

## REWORK — Directional SHADOW strategy family
Immediate cross-venue arbitrage edge and forecast-dependent directional edge are different quantities. Applying route gross profit uniformly risks treating current quote economics as a forecast of later exit value.

## IMPROVE — Strategy Lab outcome accounting
A negative filled trade is a STRATEGY/MARKET loss. Quote failure, simulation rejection, reverted transaction, dropped Solana transaction, RPC outage or accounting uncertainty is an EXECUTION/INFRASTRUCTURE failure. Combining them obscures whether to retire a strategy or repair execution.

## SHADOW_MORE — Cross Venue Net Arbitrage
Atomic EVM arbitrage is the best architectural match for current-route gross edge, but profitability depends on wallet-specific gas, builder/priority payments, state drift, reverts and inclusion.
