# GPT strategy review

Architecture-only review completed. The repository generally fails closed and emphasizes executable net edge, sellability, simulation, and money-weighted outcomes. However, Solana SHADOW inputs deliberately lack current executable economics, while Strategy Lab accounting does not structurally itemize every required cost or separate strategy/market losses from execution/infrastructure failures. Stale or missing runtime forensics prohibit profitability, CANARY-readiness, and LIVE-readiness claims.

## IMPROVE — Solana strategy families
Solana needs chain-specific round-trip quote economics, including swap fees, price impact, slippage reserve, priority/Jito fees, transaction fees, account-creation or refundable-rent treatment, latency decay, sellability, and failed-attempt cost. Historical leader returns cannot establish follower executable edge.

## REWORK — Strategy Lab evaluation
A negative realized result caused by adverse price movement is STRATEGY/MARKET loss. Reverts, dropped or expired transactions, RPC/quote failures, reconciliation failures, and paid failed attempts are EXECUTION/INFRASTRUCTURE failures. Both reduce investor P&L, but require separate remediation and must not be pooled without attribution.

## SHADOW_MORE — All cross-chain strategy families
Solana priority, account, and quote economics differ materially from EVM gas auctions, approvals, builder payments, and revert exposure. Aggregate positive results could conceal a losing chain, venue, notional bucket, or regime.
