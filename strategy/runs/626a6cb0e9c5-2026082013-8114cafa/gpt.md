# GPT strategy review

Architecture-only review completed successfully. The repository contains useful fail-closed quote, sellability, simulation and cost controls, but no fresh runtime evidence supports profitability or promotion. Leader-copy entries on both chains can pass safety thresholds without demonstrating positive expected executable edge, SHADOW Solana fees are static, aggregate Strategy Lab metrics lack robust money-weighted and failure-attribution analysis, and the 3/8-trade canary lifecycle is too small for durable inference. These are strategy-evaluation concerns; observed market losses cannot be assessed because runtime outcomes are absent, while execution/infrastructure failures must remain separately classified.

## REWORK — SiBot leader-copy entry
A safe and sellable trade can still have negative expectancy. Copying a leader after latency should require a chain-specific forecast whose conservative expected return exceeds entry and exit costs, impact, failure probability and uncertainty reserve.

## IMPROVE — Solana SiBot SHADOW accounting
Static fees can materially overstate edge during congestion and do not capture route-specific platform fees, priority spend, failed-transaction burn or capital tied in refundable rent.

## IMPROVE — Cross-chain Strategy Lab evaluation
Durability assessment requires trade-level money weighting and explicit attribution. Reverts, RPC failures, stale quotes and inclusion failures must not be mistaken for market losses, yet their incurred costs still reduce economic P&L.

## SHADOW_MORE — Strategy lifecycle promotion
Three or eight outcomes cannot establish durable net expectancy, especially when trade sizes, chain costs and market regimes differ.
