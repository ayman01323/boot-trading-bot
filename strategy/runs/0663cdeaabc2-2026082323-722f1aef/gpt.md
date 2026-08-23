# GPT strategy review

Architecture-only review completed. The repository correctly makes executable net edge, liquidity, sellability and simulation prerequisites and evaluates money-weighted net results rather than win count. However, current evidence contains no runtime forensics, and Solana Strategy Lab inputs deliberately lack contemporaneous executable economics. No strategy is proven profitable, canary-ready or live-ready. Shadow accounting should more explicitly model failed-attempt costs and classify STRATEGY/MARKET losses separately from EXECUTION/INFRASTRUCTURE failures.

## IMPROVE — All Strategy Lab families
A signal can have valid market logic yet lose because execution failed, while an executed signal can lose despite sound infrastructure. Separate MARKET/STRATEGY outcome, EXECUTION/INFRASTRUCTURE failure, and actual failed-attempt cost so strategy replacement decisions are not confounded.

## SHADOW_MORE — Solana leader-copy and market-native families
Solana strategy assessment needs a decision-time round-trip quote and a delayed counterfactual exit outcome. This must include Jupiter impact, slippage, base and priority fees, tips, platform fees, rent net of reclaim, latency deterioration and failed-attempt costs.

## SHADOW_MORE — Cross Venue Net Arbitrage
The EVM atomic-cycle architecture is economically guarded, but profitability still requires out-of-sample evidence across chain-specific gas regimes, contention and quote decay. Cross-DEX sequential execution should remain dormant until atomic.
