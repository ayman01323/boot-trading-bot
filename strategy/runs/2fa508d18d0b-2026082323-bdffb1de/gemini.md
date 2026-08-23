# Gemini strategy review

Architecture-only review completed. The repository generally enforces positive executable edge, chain-specific cost controls, and SHADOW-first governance, but Strategy Lab can treat BROADCAST, fee-pending, or expected EVM results as completed LIVE economic evidence. This can contaminate money-weighted evaluation and obscure STRATEGY/MARKET loss versus EXECUTION/INFRASTRUCTURE failure. No profitability, CANARY-readiness, or LIVE-readiness conclusion is possible without fresh runtime forensics.

## REWORK — Strategy Lab LIVE outcome attribution
A broadcast transaction is not a realised outcome, and an expected result is not money-weighted NET P&L. Mixing these states can create false profit or loss evidence. Market-negative reconciled outcomes should be classified STRATEGY/MARKET; reverted, dropped, unresolved, RPC-failed or reconciliation-incomplete attempts should be classified EXECUTION/INFRASTRUCTURE and excluded from realised P&L until resolved.

## IMPROVE — Cross-chain executable-edge SHADOW scorecards
Cross-chain ranking in native units or with generic cost buckets is not economically comparable. Solana edge must reserve priority/Jito/platform and route costs; EVM edge must include gas, builder/MEV exposure and revert cost. Discovery quotes with zero gas must never be interpreted as executable net edge.

## SHADOW_MORE — SiBot leader-copy with follower-execution decay
A historically profitable leader can be unprofitable for the follower after latency and chain-specific costs. The strategy should be judged on follower money-weighted net edge conditional on signal age and deterioration, not leader win rate. Negative reconciled follower outcomes with successful execution are STRATEGY/MARKET losses; failed or unresolved execution attempts are EXECUTION/INFRASTRUCTURE failures.
