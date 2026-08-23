# Gemini strategy review

Completed architecture-only strategy review. The codebase is structurally sound and fails closed on absent edge, but lacks realized out-of-sample forward outcome tracking, suffers from a price-impact double-subtraction defect in the net edge calculation, and permanently disqualifies Solana leader-copy signals due to a missing live quote feature adapter. No claims regarding strategy profitability, canary-readiness, or live-readiness can be made due to missing runtime evidence. We propose specific improvements, rework, and research plans to address these gaps.

## IMPROVE — Strategy Lab outcome attribution
Quote simulations are not realized P&L. To avoid selection bias, we must build a forward-only outcome ledger that records all decisions (including abstentions and failures) and maps them to actual on-chain results. Crucially, we must distinguish market/strategy losses (adverse movement post-successful fill) from execution/infrastructure failures (inclusion failure, simulation revert, RPC timeouts, gas exhaustion).

## REWORK — Common executable-edge calculation
A DEX quote for a given notional size inherently reflects the static price impact of that swap size on the pool. Deducting price_impact_bps again in net_edge_bps represents a double-subtraction. This overly conservative math suppresses genuine executable edge. Price impact should be tracked as a diagnostic filter (e.g. pool risk), while slippage reserve is kept as a dynamic buffer for quote-to-fill deviation.

## NEW_SHADOW — Solana leader-copy executable-edge adapter
The Strategy Lab cannot evaluate Solana leader-copy strategies because the features are fail-closed at zero. However, solana_sibot.py already implements jupiter_quote() for its internal copy engine. We can build a shadow adapter that queries Jupiter buy and sell quotes in real-time upon leader-event detection, populating gross_edge_bps, liquidity, and sellability without executing real trades.

## RESEARCH_MORE — Leader and strategy capital-efficiency ranking
Absolute native profit rankings favor larger capital pools and ignore capital-time exposure. High absolute profit with low capital-time efficiency or large drawdowns is highly risky. Ranks should be normalized using money-weighted returns and capital-at-risk, enabling optimal cross-chain capital allocation.
