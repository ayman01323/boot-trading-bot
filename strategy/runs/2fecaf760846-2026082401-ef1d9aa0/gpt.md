# GPT strategy review

Architecture-only review completed. The repository generally enforces positive executable edge and chain-specific cost inputs, but Strategy Lab accounting can classify BROADCAST rows using expected rather than realised P&L, omit profit-share cost, and exclude failed executions from lifecycle metrics. These defects can confuse STRATEGY/MARKET loss with EXECUTION/INFRASTRUCTURE failure and overstate durable money-weighted net performance. Solana SHADOW market features deliberately lack contemporaneous executable edge. With runtime forensics unavailable, no profitability, CANARY-readiness or LIVE-readiness claim is supported.

## REWORK — Strategy Lab lifecycle accounting
Promotion and replacement decisions must use settled, money-weighted net outcomes after every attributable cost. Expected outcomes and unconfirmed broadcasts are research/execution observations, not realised P&L.

## IMPROVE — Strategy Lab execution attribution
A negative settled trade after faithful execution is STRATEGY/MARKET loss. Revert, timeout, stale quote, RPC failure, reconciliation mismatch or abnormal realised-versus-quoted deterioration is EXECUTION/INFRASTRUCTURE failure. They require different remedies.

## NEW_SHADOW — Solana contemporaneous executable-edge measurement
Historical leader returns do not prove a follower can enter and exit profitably after copy delay, Jupiter route economics, priority fees and price impact.

## IMPROVE — Strategy Lab promotion criteria
Eight observations can be dominated by one outlier, one chain or one transient regime. Solana and EVM have materially different cost and failure distributions and must not cross-subsidise promotion evidence.
