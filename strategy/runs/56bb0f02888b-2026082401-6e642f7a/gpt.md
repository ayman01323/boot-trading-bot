# GPT strategy review

Architecture-only review completed. The repository correctly defaults missing features to fail-closed values, keeps new strategies in SHADOW, requires positive estimated executable edge, and separates simulated quotes from promotion evidence. However, there is no fresh runtime evidence, Solana lacks a current executable-edge feature adapter, and lifecycle accounting does not preserve sufficient cost and failure attribution to distinguish STRATEGY/MARKET losses from EXECUTION/INFRASTRUCTURE failures. Profitability, CANARY readiness and LIVE readiness cannot be claimed.

## IMPROVE — All Strategy Lab strategies
Durable money-weighted evaluation requires consistent denomination and explicit decomposition of market return, DEX/platform fees, slippage, price impact, EVM gas or Solana priority/base fees, refundable rent, failed-attempt cost and execution outcome. A negative result caused by adverse post-entry movement is STRATEGY/MARKET loss; quote expiry, revert, dropped transaction, RPC failure or unplanned execution cost is EXECUTION/INFRASTRUCTURE failure.

## NEW_SHADOW — Solana executable-edge adapter
Solana strategy evaluation currently measures scan coverage rather than executable economics. A SHADOW adapter should capture size-matched entry and exit quotes, base and priority fees, price impact, slippage reserve, quote age, sellability, route availability and subsequent mark-out without submitting transactions.

## SHADOW_MORE — Cross Venue Net Arbitrage and related executable-edge strategies
A fixed 4–6 bps predicted margin may be adequate for neither chain under volatile costs. Thresholds should be learned only from out-of-sample prediction error and execution shortfall, independently by chain, route, notional and liquidity regime, while retaining a strictly positive conservative edge.
