# GPT strategy review

Architecture-only review completed and supplied evidence hash verified. The repository correctly defaults new strategies to SHADOW, requires positive estimated executable edge, and does not treat absent opportunities as strategy failure. However, promotion evaluation can aggregate unlike chains, computes profit factor from gross trading gains/losses without allocating costs to losing outcomes, and does not block promotion when execution failures exist. Solana SHADOW inputs deliberately lack current executable edge and therefore cannot currently validate any Solana strategy. No profitability, CANARY-readiness, or LIVE-readiness claim is supported.

## REWORK — Strategy Lab evaluation accounting
Promotion must depend on money-weighted outcome-level net returns after every attributable fee, slippage, price-impact reserve, priority/gas fee and failed-attempt cost. Raw gross profit factor can pass while net outcome profit factor does not.

## NEW_SHADOW — Solana executable-edge outcome adapter
Leader history is not an executable follower edge. Solana requires decision-time Jupiter quote economics, priority/base fees, price impact, slippage, detection/copy delay and a later executable exit valuation to distinguish MARKET/STRATEGY loss from EXECUTION/INFRASTRUCTURE failure.

## IMPROVE — Chain-specific lifecycle evaluation and failure attribution
Solana priority-fee/Jupiter economics and EVM gas/atomic-route economics are not interchangeable. Aggregation can hide a losing chain behind a profitable one, while unresolved execution failures can contaminate strategy P&L and promotion decisions.
