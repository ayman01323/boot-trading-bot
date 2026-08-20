# GPT strategy review

Architecture-only review completed. The repository generally fails closed, requires positive modeled edge, and keeps quote simulations separate from promotion evidence. However, Strategy Lab accounting does not fully represent chain-specific all-in costs or explicitly attribute STRATEGY/MARKET losses versus EXECUTION/INFRASTRUCTURE failures. Solana research also lacks contemporaneous executable-edge features. No profitability, CANARY-readiness, or LIVE-readiness conclusion is possible without fresh runtime forensics.

## REWORK — Strategy Lab evaluation and lifecycle governance
Durable money-weighted performance requires complete all-in economic accounting. Market movement after a valid fill is a STRATEGY/MARKET outcome; reverts, RPC faults, stale-blockhash expiry, quote-to-fill degradation and missing confirmations are EXECUTION/INFRASTRUCTURE outcomes. Both can lose capital, but they require different remediation.

## NEW_SHADOW — Solana executable-edge and leader-copy validation
A copied leader's historical success is not executable edge for a delayed follower. Solana economics require current round-trip sellability, price impact, route freshness, priority fees, account creation/rent treatment and failed-transaction costs.

## IMPROVE — Cross-chain SHADOW promotion criteria
In-sample aggregate profit can hide regime dependence, EVM gas sensitivity, Solana congestion sensitivity, capital concentration and one-chain losses subsidized by another.
