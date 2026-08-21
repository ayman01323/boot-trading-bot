# GPT strategy review

Architecture-only review completed. The repository generally fails closed, models Solana and EVM costs separately, and prevents SHADOW quote results from directly authorizing LIVE promotion. However, lifecycle evaluation is not yet money-weighted and does not cleanly attribute STRATEGY/MARKET losses versus EXECUTION/INFRASTRUCTURE failures. Win-rate and participation thresholds can also pressure selection toward frequent winning or trading rather than durable net P&L. Runtime forensics are unavailable, so no profitability, CANARY-readiness or LIVE-readiness claim is made.

## REWORK — Strategy Lab lifecycle evaluation
A small profitable trade and a large losing trade must be weighted by committed capital and all user-borne costs. Win rate should remain diagnostic, not an eligibility objective. Low participation must not cause safety filters to be loosened when executable edge is absent.

## IMPROVE — Cross-chain outcome attribution
A negative filled trade caused by adverse market movement is a STRATEGY/MARKET loss. Reverts, stale quotes, RPC failures, dropped transactions, partial execution, confirmation ambiguity and unexpected fee escalation are EXECUTION/INFRASTRUCTURE failures. They need separate rates and cost totals while both remain charged to overall net P&L where capital was actually lost.

## SHADOW_MORE — Cross-chain executable-edge strategies
Shared strategy families are reasonable, but edge floors and reserves must be calibrated separately by chain, venue, route complexity, notional and congestion. Solana needs priority/Jito, account/rent and landing-failure economics; EVM needs gas, priority, builder/MEV, approval and revert economics.

## KEEP — Current Strategy Lab hypothesis families
No repository evidence justifies replacing these broad hypotheses before forward outcomes exist. Keeping them SHADOW-only preserves optionality without risking capital or manufacturing trades.
