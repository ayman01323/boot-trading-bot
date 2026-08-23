# GPT strategy review

Architecture-only review completed. The repository generally fails closed on absent executable edge and contains useful chain-specific protections, but the shared SHADOW framework records quote-derived estimates rather than forward realised outcomes and cannot establish durable money-weighted NET P&L. Solana SHADOW features deliberately lack current executable edge, while EVM simulated opportunities are not reconciled to later execution outcomes. Losses therefore cannot yet be reliably separated into STRATEGY/MARKET versus EXECUTION/INFRASTRUCTURE categories. No profitability, CANARY-readiness, or LIVE-readiness claim is supported.

## REWORK — Cross-chain SHADOW strategy evaluation
Strategy comparison requires non-overlapping decision-time signals joined to later executable exits. Measure money-weighted net return after all chain-specific costs, rejected/failed attempts, and capital occupancy; do not rank strategies by signal count or win rate.

## NEW_SHADOW — Solana leader-following and market-native signals
Historical leader profitability is STRATEGY/MARKET context, not a current executable follower edge. Solana needs decision-time entry and reverse-exit economics reflecting Jupiter routes, priority fees, price impact, slippage, latency decay, token-account/rent effects, and failed attempts.

## IMPROVE — EVM cross-venue arbitrage and learned-route replication
A scanner quote and simulation are opportunity evidence, not an outcome. EVM economics must include approval transactions, reverted or dropped attempts, replacement fees, EIP-1559 bid effects, builder payments, stale-route decay, and any profit-share settlement cost.

## RESEARCH_MORE — Failure-aware portfolio attribution
Durable strategy selection requires mutually exclusive primary attribution: STRATEGY/MARKET loss when execution matched the decision but price evolution was adverse, versus EXECUTION/INFRASTRUCTURE failure when quote, RPC, construction, signing, simulation, broadcast, inclusion, reconciliation, or exit handling deviated.

## KEEP — Executable-edge and sellability safeguards
These protections align with abstaining when positive executable edge cannot be demonstrated and must not be weakened while gathering evidence.
