# GPT strategy review

Architecture-only review completed. The repository contains useful fail-closed quote, simulation, liquidity, sellability and cost gates, but no fresh runtime forensics support profitability, CANARY or LIVE readiness. Solana Strategy Lab currently abstains because it cannot calculate contemporaneous executable edge. EVM realised accounting covers successful cycle gas, but broadcast failures can be recorded as REJECTED without failed-transaction gas, obscuring EXECUTION/INFRASTRUCTURE loss. Canary graduation thresholds are too small to establish durable money-weighted net performance. These are distinct from STRATEGY/MARKET losses, which require confirmed executions with complete cost-adjusted outcomes.

## KEEP — Cross-chain positive executable edge gating
The bot should abstain whenever positive executable edge cannot be demonstrated. These controls align with net P&L rather than win count.

## NEW_SHADOW — Solana leader-copy executable edge
Historical leader returns cannot prove that a delayed follower entry remains profitable after Jupiter impact, slippage, priority fees, rent exposure and exit costs.

## REWORK — Cross-chain execution-cost attribution
A confirmed trade losing after complete costs is STRATEGY/MARKET loss. RPC, simulation, landing, receipt and status-0 failures are EXECUTION/INFRASTRUCTURE events; any irreversible fees they consume must still reduce net P&L.

## REWORK — Strategy promotion evidence contract
Three or eight trades can be dominated by one outcome and cannot establish durable failure-adjusted, money-weighted net profitability.

## SHADOW_MORE — EVM cross-venue and single-router cycle arbitrage
Atomic minimum-output protection limits market loss but does not eliminate reverted-transaction gas, state-race losses, stale quotes or infrastructure failures.
