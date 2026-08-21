# GPT strategy review

Architecture-only review completed. The common signal layer correctly requires positive cost-adjusted edge and fails closed on missing Solana executable evidence. However, Strategy Lab can overstate money-weighted NET P&L by accepting BROADCAST/expected EVM results and by recording leader-copy profit before user profit-share costs. Runtime forensics are unavailable, so no strategy is shown profitable, CANARY-ready or LIVE-ready. Loss attribution must separately classify adverse post-entry price movement as STRATEGY/MARKET loss and reverts, timeouts, RPC faults, stale quotes, nonce faults, reconciliation gaps and fee-settlement failures as EXECUTION/INFRASTRUCTURE failure.

## IMPROVE — Strategy Lab realised NET P&L accounting
Promotion evidence must represent wallet-level, money-weighted net returns after every execution and service cost. Expected outcomes and pre-fee results can inflate profit factor and misclassify operational failures as neutral trades.

## SHADOW_MORE — Solana leader-copy and common Strategy Lab signals
Solana economics require contemporaneous executable buy and sell quotes, size-dependent impact, priority/base fees, token-account or refundable-rent treatment, latency and sellability. Historical leader win ratios are not current executable edge.

## SHADOW_MORE — EVM cross-venue arbitrage and learned-route signals
The architecture has appropriate pre-trade gates, but durable edge requires out-of-sample quote-to-fill calibration and enough observations to estimate tail losses and failure costs. Tiny samples and an apparently disconnected result recorder cannot establish promotion readiness.
