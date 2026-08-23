# GPT strategy review

Architecture-only review completed. The repository correctly favors abstention and contains chain-specific execution guards, but promotion evidence can be overstated because Strategy Lab accepts caller-supplied net P&L, aggregates potentially heterogeneous observations, permits promotion from only eight trades, and does not make execution failures a promotion blocker. EVM retained-user P&L and fee-settlement costs are not represented by one authoritative net field. Solana economic caps depend partly on configured expected margin rather than a calibrated opportunity-specific forecast. These are evidence-governance and accounting concerns, not proof of realised strategy loss. No fresh runtime evidence exists, so profitability, CANARY readiness and LIVE readiness cannot be claimed.

## REWORK — Strategy Lab evaluation and promotion governance
Money-weighted decisions require reproducible, chain- and cost-complete accounting. Caller-supplied net values and pooled small samples can hide cost omissions, regime dependence or execution failures.

## IMPROVE — EVM atomic cycle arbitrage
The objective is durable user money-weighted net P&L. Cycle-level execution profit, platform fee liability and fee-settlement gas must be reconciled without conflating market loss with execution failure.

## SHADOW_MORE — Solana leader-copy positive executable edge
Historical leader medians and platform profit factor are useful priors but do not prove that a particular delayed follower entry retains positive executable edge. Static expected-margin assumptions can misallocate the fee budget across opportunities.

## KEEP — Cross-chain SHADOW signal families
Explicit abstention and chain-adapter cost inputs align with the objective better than maximizing trade count or win rate.
