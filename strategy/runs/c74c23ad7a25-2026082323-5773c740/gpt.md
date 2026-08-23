# GPT strategy review

Architecture-only review completed. The repository generally enforces abstention and cost-adjusted executable edge, but current evidence cannot establish profitability or CANARY/LIVE readiness. Solana deliberately lacks a current executable-edge adapter. EVM shadow outcomes are simulations, while LIVE aggregation can substitute expected P&L for missing realised P&L and excludes rejected execution attempts, preventing reliable separation of STRATEGY/MARKET loss from EXECUTION/INFRASTRUCTURE failure.

## REWORK — Strategy Lab outcome accounting
Expected P&L is not realised money-weighted evidence, BROADCAST is not confirmed success, and omitted failed attempts can bias strategy evaluation upward. Record confirmed realised net after every applicable cost and classify each non-success as STRATEGY_MARKET, EXECUTION_INFRASTRUCTURE or SAFETY_ABSTENTION.

## NEW_SHADOW — Solana executable-edge adapter
Leader history or win ratio cannot demonstrate current executable edge. Solana needs decision-time round-trip quote, sellability and cost evidence including priority fees, account rent where applicable, price impact, latency reserve and failed-attempt cost.

## REWORK — Learned Route Replication
A positive-only historical mean creates survivorship bias, and rescaling absolute historical profit by current notional does not produce a valid historical return estimate.

## SHADOW_MORE — All cross-chain strategy families
Eight trades cannot establish durable money-weighted edge, and pooled results can hide materially different Solana and EVM economics.
