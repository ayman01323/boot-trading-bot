# GPT strategy review

Architecture-only review completed. The repository has useful fail-closed quote, simulation, liquidity and sellability controls, and Solana Strategy Lab inputs correctly refuse to invent executable edge. However, lifecycle accounting can aggregate incomparable native currencies, accept caller-supplied net P&L without reconciliation, and calculate profit factor before costs. Execution failures are counted separately but not sufficiently classified from STRATEGY/MARKET losses. No profitability, CANARY-readiness or LIVE-readiness conclusion is possible without fresh runtime forensics.

## REWORK — Strategy Lab lifecycle evaluation
Money-weighted promotion evidence must not add SOL, ETH and other native-token amounts as if they shared a unit. It must also prevent inconsistent submitted net values and overlapping windows from overstating results.

## IMPROVE — Loss forensics and lifecycle attribution
STRATEGY/MARKET loss means a successfully executable position lost after all costs because the signal or market path was unfavorable. EXECUTION/INFRASTRUCTURE failure means intended execution was not completed or settlement evidence is unreliable. Combining these obscures whether to rework alpha, routing or infrastructure; failed attempts can also consume fees and must affect NET P&L.

## SHADOW_MORE — Solana leader-copy
The fail-closed Strategy Lab behavior should be kept, but fixed priority-fee estimates cannot demonstrate durable executable edge under congestion. Jupiter quote output captures route economics only at observation time and does not establish landing probability, quote-to-fill decay, failed-attempt cost or future exit sellability.

## SHADOW_MORE — EVM route and copy strategies
The EVM prebroadcast protections are worth keeping, but chain-specific gas markets, native-token values, block times, MEV exposure and failure rates require separate evidence. Eight favorable small observations are not durable evidence by themselves.
