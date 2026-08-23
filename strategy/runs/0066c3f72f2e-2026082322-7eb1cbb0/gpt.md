# GPT strategy review

Architecture-only review completed. The repository has fail-closed SHADOW evaluation, positive-edge checks, and substantial chain-specific execution protection, but current Strategy Lab accounting cannot establish durable money-weighted net profitability. It can aggregate unlike native currencies, computes profit factor from gross outcomes rather than fully costed net outcomes, has no executable Solana SHADOW outcome adapter, and does not consistently monetize and classify execution failures separately from strategy/market losses. Runtime forensics are unavailable, so no strategy is claimed profitable, canary-ready, or live-ready.

## REWORK — Strategy Lab portfolio evaluation
SOL, ETH, MATIC and other base amounts are not additive. Promotion evidence should be partitioned by chain and strategy version, then money-weighted in a documented common numeraire. Profit factor should use per-trade fully costed net gains and net losses, including fees, slippage, price impact, priority or gas fees, and failed-attempt costs.

## NEW_SHADOW — Solana executable-edge observation
The shared strategy families currently cannot be economically evaluated on Solana. A non-signing adapter should capture decision-time Jupiter quotes and sellability/liquidity evidence, then measure future counterfactual exits without hindsight. Costs must include platform fees, price impact, slippage, priority fees or tips, base fees, account/rent effects when non-refundable, and failed-attempt probability.

## IMPROVE — Cross-chain loss attribution
Strategy selection must not treat reverted gas, stale quotes, landing failures, excess slippage, or infrastructure faults as equivalent to a correctly executed trade whose market thesis lost. Conversely, execution labels must not excuse adverse selection without matched evidence.

## RESEARCH_MORE — EVM executable route evaluation
The architecture may conservatively double-count AMM price impact when quoted output already embeds it. This must be established from field semantics per route type before changing any gate.
