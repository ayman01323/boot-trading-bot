# GPT strategy review

Architecture-only review completed successfully. Current executable-edge, sellability and simulation gates are appropriately fail-closed, but Learned Route Replication has positive-only selection bias and outcome accounting does not yet establish durable money-weighted user NET P&L. Solana leader copying remains SHADOW and lacks contemporaneous forward edge plus measured priority-fee, price-impact and account-rent economics. Strategy/market losses must be recorded separately from RPC, quote, simulation, broadcast, confirmation and reconciliation failures. With runtime forensics unavailable, no strategy is claimed profitable, canary-ready or live-ready.

## REWORK — Learned Route Replication
This is a STRATEGY/MARKET evidence defect: losing executions of the same route are excluded from the learned mean, creating survivor bias and potentially treating a negative-expectancy route as historically positive.

## SHADOW_MORE — Solana Leader Copy
Leader profitability is historical context, not executable follower edge. Market loss from adverse post-entry price movement must be separated from infrastructure failures such as stale quotes, Jupiter/RPC errors, simulation rejection, expired blockhash, failed landing or unreconciled balances.

## IMPROVE — EVM Atomic Route Strategies
A landed negative-net transaction is a STRATEGY/MARKET loss when execution matched the approved route and economics simply deteriorated. Quote, simulation, RPC, nonce, broadcast, confirmation or reconciliation faults are EXECUTION/INFRASTRUCTURE failures. Ambiguous post-broadcast states must not become strategy losses or cost-free rejections.

## KEEP — Cross-Chain Executable Edge Gate
These controls correctly prefer no trade over unsupported edge and preserve chain-specific cost inputs. They should remain mandatory while the evidence and accounting improvements are tested.
