# GPT strategy review

Architecture-only review completed. The repository contains fail-closed quote, liquidity, sellability and simulation controls, with materially different Solana and EVM execution economics. However, Strategy Lab accounting can label results promotion candidates without first-class gas, priority/tip, price-impact, rent, failed-attempt and capital-weighted fields. Solana strategy features intentionally lack executable edge. No profitability, CANARY-readiness or LIVE-readiness claim is supportable without fresh runtime forensics.

## REWORK — Strategy Lab portfolio evaluation
The review objective is durable money-weighted net P&L. Aggregating heterogeneous costs into optional caller-supplied net values permits optimistic or incomparable evaluations and does not measure return on deployed capital.

## NEW_SHADOW — Solana executable-edge adapter
Leader-copy observations alone cannot establish forward executable edge. Solana needs a SHADOW adapter using contemporaneous round-trip quotes and its own fee, priority/tip, rent and failure economics.

## IMPROVE — Cross-chain strategy outcome attribution
STRATEGY/MARKET loss means a successfully executable thesis produced negative net P&L or its edge decayed. EXECUTION/INFRASTRUCTURE failure means quote, RPC, simulation, signing, broadcast, landing or reconciliation failed. Combining them can replace a sound hypothesis for an infrastructure defect or hide costly failed attempts.

## SHADOW_MORE — EVM cross-chain signal canary lane
The execution gates are directionally strong, but three and eight outcomes cannot demonstrate durable money-weighted performance across fee and liquidity regimes.
