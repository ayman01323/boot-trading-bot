# GPT strategy review

Architecture-only review completed. The repository generally fails closed and separates SHADOW research from signing, but its Strategy Lab accounting and promotion-candidate evaluation do not yet prove durable money-weighted net P&L. Runtime forensics are missing, so no strategy is claimed profitable, canary-ready, or live-ready. Solana currently lacks contemporaneous executable-edge SHADOW inputs; EVM has quote/simulation economics but not realised out-of-sample outcomes. Execution/infrastructure failures must be reported separately from strategy/market losses while still charging all economically incurred failure costs to net P&L.

## REWORK — Strategy Lab evaluation and lifecycle governance
Aggregate nominal profit can favor larger or longer-exposed positions and can overstate edge when failed attempts or chain-specific costs are omitted. Classification should identify STRATEGY/MARKET losses separately from EXECUTION/INFRASTRUCTURE failures, while the latter's paid fees and adverse inventory effects remain charged economically.

## NEW_SHADOW — Solana contemporaneous executable-edge adapter
Leader history is not an executable future edge. Solana needs decision-time entry and exit quotes, route liquidity/sellability, impact, platform fee, priority/Jito fee, latency deterioration, and failure probability measured at the intended size.

## SHADOW_MORE — EVM cross-venue and learned-route strategies
EVM architecture estimates executable economics conservatively, but durable edge requires later outcome capture including base-fee changes, priority fees, reverts, replacement transactions, stale quotes, and opportunity decay.

## KEEP — Chain-specific executability and abstention protections
These controls embody the required abstention rule and chain-specific economics. No architectural evidence justifies weakening them to increase trade count.
