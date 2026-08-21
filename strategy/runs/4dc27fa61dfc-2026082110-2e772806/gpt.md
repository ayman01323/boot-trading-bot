# GPT strategy review

Architecture-only review completed. The repository contains useful fail-closed quote, simulation, liquidity, sellability and cost controls, but no fresh runtime evidence supports profitability or CANARY/LIVE readiness. Solana has detailed fee, priority-fee, impact, slippage and refundable-rent accounting, yet its Strategy Lab adapter deliberately lacks current executable edge. EVM SiBot validates entry deterioration and round-trip sellability but does not require a forecast return exceeding the complete follower round-trip cost. Strategy/market losses and execution/infrastructure failures are counted separately in some lifecycle code, but the failure taxonomy is too coarse for reliable attribution.

## REWORK — EVM SiBot leader-copy entry
A sellable route can still have negative executable expectancy. Historical leader profitability and a maximum friction threshold do not establish positive follower edge after copying latency and chain costs.

## NEW_SHADOW — Solana current executable-edge adapter
Solana Strategy Lab cannot presently test positive executable edge using the richer economics already available elsewhere. Historical leader returns must not substitute for a current follower edge.

## IMPROVE — Cross-chain outcome attribution
The high-level distinction is correct, but a Boolean execution-failure flag cannot distinguish negative market edge from stale quotes, simulation rejection, RPC failure, broadcast failure, revert, dropped transaction, confirmation timeout, partial fill, accounting uncertainty or failed exit.

## SHADOW_MORE — Strategy Lab lifecycle thresholds
Three to eight trades are insufficient to demonstrate durable money-weighted net edge across regimes, particularly when fixed gas or priority fees dominate small notionals. No promotion is supportable from the supplied evidence.
