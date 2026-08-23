# Gemini strategy review

Architecture-only review completed. Strategy Lab successfully isolates risk in SHADOW but currently writes zero fees and zero slippage into live strategy windows due to a logging bug in the live recording patch. Furthermore, Solana strategies are structurally stalled in SHADOW because the market feature adapter lacks a contemporaneous quote mechanism, forcing all economic and liquidity metrics to zero. A notional-aware edge denominator and detailed terminal loss classification are proposed to resolve these structural validation issues before any real-money canary promotion can be considered.

## IMPROVE — Strategy Lab portfolio evaluation
Durable money-weighted net P&L must incorporate every network, router, and platform fee. Storing zero fees and slippage masks execution overhead, which can erroneously promote unprofitable strategies to the canary stage.

## IMPROVE — Solana leader-market observations
Because Solana leader events are stored without real-time executable pricing quotes, Solana strategies are structurally stuck in SHADOW with zero eligible opportunities. Adding a contemporaneous quote adapter (fetching live Jupiter/Raydium swap routes during shadow scanning) resolves this evidence gap and enables valid shadow testing.

## REWORK — Cross-chain executable-edge strategies
Solana priority fees and EVM gas are fixed or state-dependent native-token costs whose basis-point burden changes with notional. A common static bps edge floor can be positive while executable user net is negative, especially at lower notional trade sizes.

## SHADOW_MORE — Solana and EVM leader-copy and market-native strategies
A negative return after a correctly executed signal is a STRATEGY_MARKET_LOSS, whereas quote failures, stale detection, submission delays, or transaction reverts represent EXECUTION_INFRA_FAILURE. Distinguishing these causes is vital for directing engineering effort (reworking signal rules vs. improving RPCs and routing).
