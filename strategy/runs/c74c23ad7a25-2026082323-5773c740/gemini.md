# Gemini strategy review

This strategy review has been completed using a pure architecture-only evaluation because the latest runtime evidence is unavailable (MISSING_RUNTIME_FORENSICS). Consequently, no claims of live profitability, canary readiness, or live promotion are made. The review covers the multi-chain spot-copy engine (SiBot) on EVM and Solana, as well as the 7 built-in Strategy Lab market/pattern-learning families. We propose maintaining all active strategies under strict SHADOW evaluation to gather high-fidelity data, while introducing key structural improvements and a new volatility-dislocated mean reversion shadow strategy.

## SHADOW_MORE — SiBot / SiMo Leader Copy Trading
Due to missing runtime forensics in evidence.json, we cannot assess active copy-trading performance or leader profitability. SIBOT is highly mature with precise FIFO matching and stop-loss/take-profit guards, but it must remain strictly in SHADOW mode across both EVM and Solana to reconstruct long-term wallet history and measure leader persistence.

## SHADOW_MORE — Cross Venue Net Arbitrage
This strategy relies on fleeting price differences between venues. Real-world execution requires ultra-low latency. Because runtime forensics and RPC latencies are missing from our environment, we must evaluate this strategy purely in SHADOW mode to determine if typical RPC delays (e.g. polygon or solana network congestion) would invalidate the net edge (currently set to 4 basis points minimum).

## SHADOW_MORE — Liquidity Confirmed Momentum
Confirming momentum via liquidity and flow expansion protects the bot from buying into illiquid, highly-maniputable tokens. In the absence of live trading diagnostics, we must keep this strategy under SHADOW evaluation to calibrates optimal z-score thresholds (momentum_z >= 1.0, flow_acceleration_z >= 0.5) across different market regimes.

## NEW_SHADOW — Volatility Dislocated Mean Reversion
We propose a new shadow strategy variant that specifically targets localized, high-velocity price dislocations on liquid pairs during sudden volatility spikes, provided the volatility remains within a bounded moderate range (volatility_z between 1.0 and 2.5). This avoids the 'falling knife' scenarios of extreme volatility (volatility_z > 3.0 is rejected by standard reversion) while exploiting short-term liquidity imbalances that are highly likely to revert.
