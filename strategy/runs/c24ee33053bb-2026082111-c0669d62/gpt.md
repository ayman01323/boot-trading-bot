# GPT strategy review

Architecture-only review completed. No profitability, CANARY-readiness, or LIVE-readiness conclusion is possible because evidence.json explicitly reports MISSING_RUNTIME_FORENSICS. The repository generally fails closed when executable evidence is absent, keeps SHADOW simulation separate from promotion evidence, and requires positive cost-adjusted edge. However, durable money-weighted evaluation is incomplete: Solana has no executable quote/outcome adapter; SHADOW accounting does not preserve all cost components or realised outcomes; strategy loss versus execution/infrastructure failure lacks a durable taxonomy; and generic canary state can aggregate native-denominated results across EVM chains. Preserve all wallet, signing, LIVE/ARMED, simulation, liquidity, sellability, reserve, stop-loss, nonce, and reconciliation protections.

## IMPROVE — All SHADOW strategy families
A positive quote-derived edge is not durable money-weighted NET P&L unless every chain-specific cost and failed-attempt cost is retained and later reconciled to an independent outcome.

## NEW_SHADOW — Solana leader-following and market-native signals
Leader history and positive ratios do not establish follower edge after copy latency, Jupiter route impact, priority fees, token-account/rent effects, sellability, and failed landing. Solana cannot currently produce executable SHADOW strategy evidence.

## IMPROVE — All strategy scorecards
Market losses should challenge the signal hypothesis, while reverted, expired, stale-quote, RPC, nonce, reconciliation, and accounting failures should challenge execution or infrastructure. Combining or dropping them corrupts strategy selection, although failure costs must still remain in portfolio NET P&L.

## REWORK — Cross Venue Net Arbitrage and other shared-name strategies
Summing native-denominated results across chains invalidates money weighting and can let one chain's economics promote another chain's strategy instance. Three or eight trades are also insufficient architecture-level evidence of durable edge.
