# GPT strategy review

Architecture-only review completed. The repository generally fails closed on absent executable edge and includes chain-specific safeguards, but the common SHADOW layer has no realised out-of-sample outcome attribution, Solana lacks a current-edge adapter, and cost-field semantics risk double-counting quote-embedded price impact. No profitability, CANARY-readiness or LIVE-readiness conclusion is possible. STRATEGY/MARKET losses must be measured only after successful execution against a valid decision-time quote; submission, inclusion, revert, stale-route and reconciliation failures must be classified separately as EXECUTION/INFRASTRUCTURE failures.

## IMPROVE — Cross-chain Strategy Lab outcome attribution
Quote-positive opportunities are not realised P&L. A forward-only ledger must distinguish market decay or adverse price movement after successful executable entry from quote, simulation, submission, inclusion, balance-reconciliation and exit failures. It should retain zero-trade abstentions and failed attempts so selection bias does not inflate results.

## REWORK — Common executable-edge calculation
Executable quote output normally reflects current pool price impact. Subtracting the stored impact again may suppress genuine edge, while failing to reserve quote-to-fill deterioration may overstate it. Each cost field needs an explicit mutually exclusive definition. EVM gas, builder fees and approval/amortised token costs must remain distinct from Solana base, priority, account/rent and Jupiter costs.

## NEW_SHADOW — Solana leader-copy executable-edge adapter
Historical leader win ratios do not prove follower edge. A SHADOW-only adapter can reuse fresh Jupiter buy/reverse quotes while explicitly reserving entry and exit priority fees, quote-to-fill deterioration, price impact and execution-failure probability. Until a forecast supplies positive future gross return, round-trip sellability alone must not be treated as positive edge.

## RESEARCH_MORE — Leader and strategy capital-efficiency ranking
Absolute native profit can favour larger deployed capital, and profit per active hour is not money-weighted return. Chain-native totals are also unsuitable for cross-chain capital allocation without decision-time valuation. Win count must remain diagnostic only.
