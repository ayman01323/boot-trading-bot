# GPT strategy review

Architecture-only review completed. The repository correctly prioritises money-weighted net results, abstains without executable edge, and provides chain-specific feature adapters. However, promotion-candidate evaluation can consume windows without proving realised out-of-sample outcomes or complete costs, and EVM recording may substitute expected net for realised net on BROADCAST or fee-pending rows. Solana deliberately lacks a current executable-edge adapter and therefore remains signal-only. No profitability, CANARY-readiness or LIVE-readiness conclusion is supported.

## REWORK — Strategy Lab promotion evaluation
Strategy/market losses and execution/infrastructure failures must remain separately measurable, while promotion must be fail-closed unless net P&L is realised, cost-complete and independently out-of-sample.

## IMPROVE — EVM direct-market arbitrage
Expected profit on a broadcast or fee-pending transaction is not realised net P&L. Reverts, replacement transactions, gas paid on failures, profit-fee settlement and final balance reconciliation materially change EVM economics.

## NEW_SHADOW — Solana leader-copy and market-native signals
Solana cannot be evaluated economically from leader BUY events alone. A chain-specific SHADOW adapter must measure executable entry and exit economics, priority fees and inclusion failures without treating historical wallet success as forward edge.
