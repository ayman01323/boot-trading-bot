# GPT strategy review

Architecture-only review completed. The repository correctly requires positive current executable edge, fails closed on missing Solana quote evidence, and prevents SHADOW auto-promotion. However, Strategy Lab accounting does not yet prove durable money-weighted net P&L: failed-execution costs are not explicitly incorporated, supplied net_profit can disagree with component costs, failure causes are not classified as STRATEGY/MARKET versus EXECUTION/INFRASTRUCTURE, and promotion evaluation aggregates observations without capital weighting or out-of-sample regime separation. Solana currently has no promotion-capable executable-edge adapter. No profitability, CANARY-readiness, or LIVE-readiness conclusion is supported.

## IMPROVE — Strategy Lab realised outcome accounting
A strategy loss is adverse market movement or invalid signal edge after successful execution. An execution failure is a revert, dropped/expired transaction, partial route, failed economic reconciliation or unsellable exit; an infrastructure failure is RPC, quote-provider, indexing or persistence failure. These must be separate while all paid costs, including failed-attempt fees, reduce strategy-level net P&L.

## NEW_SHADOW — Solana current executable-edge measurement
Leader win ratios are historical context, not current executable edge. Solana needs paired entry and executable exit quotes with chain-specific costs and account/rent economics before any strategy comparison is meaningful.

## REWORK — Cross-chain Strategy Lab promotion evaluation
Absolute profit can be dominated by larger notionals, one chain or one market regime. Durable selection requires capital-weighted returns, drawdown/tail loss, failure rate and chronological holdout evidence, with Solana and each EVM chain evaluated under their own cost distributions.
