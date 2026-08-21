# GPT strategy review

Architecture-only review completed. The repository contains useful fail-closed executable-edge, liquidity, sellability, simulation and non-signing SHADOW protections. However, Solana Strategy Lab inputs deliberately lack current executable economics, while EVM SHADOW results are simulations rather than realised outcomes. Accounting and failure attribution also do not yet provide a unified money-weighted net P&L after every user-borne cost. No profitability, CANARY-readiness or LIVE-readiness conclusion is supported.

## KEEP — Common executable-edge and SHADOW safety boundary
These controls enforce abstention when executable edge or market quality is missing and prevent simulated performance from being mistaken for realised profitability.

## NEW_SHADOW — Solana cost-calibrated copied-flow outcomes
Leader profitability does not establish follower profitability. Solana needs contemporaneous follower-sized entry and exit quotes plus priority fees, price impact, slippage, token-account/rent economics, latency and failure costs before copied-flow edge can be evaluated.

## IMPROVE — Strategy Lab outcome accounting and attribution
Durable optimization requires money-weighted user NET P&L, not win count or pre-platform-fee profit. Market losses must affect strategy estimates, while RPC, simulation, revert, expiry, insufficient-fee-reserve and submission failures must affect execution reliability and expected failure cost.

## SHADOW_MORE — EVM cross-venue and learned-route strategies
Exact quotes and successful simulations validate route feasibility but do not measure quote decay, inclusion latency, reverts, replacement fees, MEV exposure or realised fill economics.
