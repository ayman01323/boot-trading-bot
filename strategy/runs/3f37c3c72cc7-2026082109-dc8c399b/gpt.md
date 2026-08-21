# GPT strategy review

Architecture-only review completed. The repository generally fails closed when executable edge, liquidity, sellability or simulation evidence is absent, and SHADOW quote results are correctly barred from promotion evidence. However, EVM cost accounting omits explicit expected execution-failure cost, directional strategies can inherit an arbitrage quote's gross edge without a strategy-specific forward-return model, Solana lacks a current executable-edge adapter, and promotion thresholds are too small and insufficiently chain/regime segmented for durable money-weighted net P&L. No profitability, CANARY-readiness or LIVE-readiness conclusion is supported.

## REWORK — Directional and cross-venue strategy evaluators
An immediately executable arbitrage spread and an uncertain future directional return are different economic quantities. Momentum, mean-reversion and flow strategies require a strategy-specific, horizon-specific forward gross-return estimate before costs; an executable swap quote only proves present transaction terms.

## IMPROVE — Executable net-edge accounting
EVM failures can consume gas and replacement bids, while Solana failures can consume signature and priority fees and lose quote freshness. These are EXECUTION/INFRASTRUCTURE costs, distinct from STRATEGY/MARKET losses after successful fills, but both reduce durable net P&L.

## SHADOW_MORE — Solana leader-copy and market-native strategies
The fail-closed adapter is appropriate, but it means there is currently no architecture path for proving positive Solana executable edge in the common review framework. Solana evidence must include priority fees, quote impact, token-account rent cash flows, sellability and failed/retried execution costs.

## REWORK — Strategy lifecycle and promotion evidence
A few trades can produce a misleading profit factor, especially with no losses. Promotion evidence should emphasize money-weighted net P&L, drawdown and lower-confidence bounds, while separately reporting STRATEGY/MARKET losses from successful fills and EXECUTION/INFRASTRUCTURE failures.

## KEEP — Fail-closed executable-edge and SHADOW boundary
These boundaries correctly prefer abstention over unsupported edge and protect against confusing quote availability with realised profitability.
