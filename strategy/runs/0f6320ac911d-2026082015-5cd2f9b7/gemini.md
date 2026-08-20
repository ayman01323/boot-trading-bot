# Gemini strategy review

Completed architecture-only review of cross-chain strategies and lab design. No live runtime forensics were provided, so no claims on live profitability or canary readiness can be made. Shadow testing is proposed for key hypotheses to gather necessary execution and cost-model evidence.

## SHADOW_MORE — Cross Venue Net Arbitrage
Arbitrage strategies are highly sensitive to latency and execution costs. Without runtime forensics, we must validate whether the expected net edge survives actual chain conditions, particularly Solana priority fees and EVM gas dynamics.

## SHADOW_MORE — Learned Route Replication
Copying historical routes risks decay as market participants adapt. We need to shadow test if the historical average net bps persists in forward (out-of-sample) testing.

## NEW_SHADOW — Forecasted Positive Net Edge
Forecasts require explicit out-of-sample forward testing to ensure uncertainty calibration is accurate and not overconfident in noisy markets.
