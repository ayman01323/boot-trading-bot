# GPT strategy review

Architecture-only review completed. The repository correctly defaults missing Solana executable edge to zero and keeps quote simulations out of promotion evidence. However, Strategy Lab promotion and replacement decisions can use incomplete economics: LIVE EVM ingestion treats BROADCAST and expected P&L as executed outcomes, cost components are not preserved, and failure attribution is largely absent. Solana SHADOW signals currently cannot evaluate strategy edge because leader events are deliberately adapted with zero edge, liquidity and sellability. No profitability, CANARY-readiness or LIVE-readiness conclusion is supported.

## REWORK — Direct Market Arbitrage
Broadcast is not final execution, and expected P&L is not realised money-weighted P&L. Reverts, replacements, dropped transactions, partial reconciliation and final gas can turn an apparent strategy win into an EXECUTION/INFRASTRUCTURE failure or loss.

## IMPROVE — Strategy Lab portfolio evaluation
A negative outcome must be decomposed into STRATEGY/MARKET loss versus EXECUTION/INFRASTRUCTURE failure. The current aggregate cannot reliably show whether signal direction, adverse market movement, stale quotes, RPC failure, failed inclusion, gas, priority fees, slippage or liquidity caused the result.

## NEW_SHADOW — Solana leader-copy executable-edge model
Historical leader return is strategy context, not current executable edge. A SHADOW adapter can reuse preflight evidence to estimate round-trip economics without authorising trades.

## SHADOW_MORE — Cross-chain market-native strategy family
A common signal vocabulary is useful, but fixed thresholds can under-reserve expensive or slow EVM conditions and over-filter cheaper routes. Solana needs priority-fee, copy-delay and inclusion-risk reserves distinct from EVM gas and finality risk.
