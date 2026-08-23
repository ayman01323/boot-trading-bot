# GPT strategy review

Architecture-only review completed. The repository contains conservative fail-closed gates and explicitly prohibits promotion from quote simulations, but it cannot establish durable money-weighted net profitability without fresh runtime forensics. Solana SHADOW inputs intentionally lack contemporaneous executable edge, while EVM SHADOW results are derived from the same estimated edge used for eligibility rather than independent future outcomes. Historical route learning also averages only positive proven observations, creating selection bias. These are strategy-evidence limitations, not demonstrated execution or infrastructure failures. No strategy is shown profitable, canary-ready, or live-ready.

## REWORK — Learned Route Replication
A positive-only estimator overstates route expectancy and can select routes that lose money after failed execution, revert gas, slippage and adverse price movement.

## IMPROVE — Cross-chain SHADOW strategy scorecard
Independent post-decision outcomes are required to separate STRATEGY/MARKET losses from EXECUTION/INFRASTRUCTURE failures and to measure net expectancy without circular scoring.

## NEW_SHADOW — Solana Leader Copy Executable Edge
Copy profitability depends on follower quote timing, route liquidity, entry deterioration, priority fees and exit sellability, not merely the leader's historical return distribution.

## SHADOW_MORE — Cross Venue Net Arbitrage and Forecasted Positive Net Edge
A fixed four-to-six basis-point threshold cannot represent chain-specific fixed fees, EVM revert risk, Solana priority-fee variance, latency decay or estimation error.
