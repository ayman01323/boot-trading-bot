# GPT strategy review

Architecture-only review completed. Both chains contain fail-closed execution protections and cost-aware accounting, but no fresh runtime forensics exist, so profitability and CANARY/LIVE readiness are unproven. Solana Strategy Lab correctly refuses to infer executable edge from leader events. EVM route simulation protects minimum net output, but strategy promotion thresholds are too small to establish durable money-weighted net profitability. Strategy/market losses must be measured separately from infrastructure failures, while still charging all failed-transaction costs to portfolio P&L.

## KEEP — Solana leader-copy and cross-chain Strategy Lab signals
The architecture appropriately abstains when a leader event lacks a contemporaneous executable round-trip edge. This prevents historical leader win rates from being mistaken for follower profitability.

## REWORK — Fast strategy canary promotion
Three or eight trades can produce a misleading positive profit factor through variance or one outsized result. Durable money-weighted net profitability requires substantially broader out-of-sample evidence.

## SHADOW_MORE — Cross Venue Net Arbitrage and directional cross-chain signals
Fixed edge floors are hypotheses, not demonstrated safety margins. EVM gas/MEV exposure and Solana priority fees, account rent, quote decay, route impact, and landing risk require separate empirical error reserves.

## IMPROVE — Portfolio net-P&L attribution
Cause attribution should distinguish STRATEGY/MARKET loss from EXECUTION/INFRASTRUCTURE failure, but portfolio money-weighted P&L must still include gas, priority fees, tips, and other irreversible costs from failures.
