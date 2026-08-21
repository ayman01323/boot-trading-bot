# GPT strategy review

Architecture-only review completed. Existing fail-closed Solana feature adaptation, EVM re-quote/simulation, sellability checks, and receipt-based accounting are sound foundations, but there is no fresh runtime evidence supporting profitability or promotion. The principal gap is a unified out-of-sample, money-weighted NET P&L outcome layer that attributes STRATEGY/MARKET losses separately from EXECUTION/INFRASTRUCTURE failures and includes all chain-specific costs. No strategy is shown to be profitable, canary-ready, or live-ready.

## IMPROVE — Cross-chain Strategy Lab outcome attribution
Durable strategy selection requires out-of-sample money-weighted NET P&L after every paid and implicit execution cost. Market losses must remain strategy outcomes, while reverted transactions, RPC faults, stale quotes, nonce faults, reconciliation uncertainty, and unavailable exits must be measured as execution or infrastructure failures without disguising their economic cost.

## SHADOW_MORE — Solana leader-copy and liquidity strategies
Historical leader returns are not a contemporaneous executable edge. Solana hypotheses need decision-time Jupiter route quotes, sellability probes, price impact, priority/platform fees, expected failure cost, latency decay, and rent exposure before any positive-edge conclusion.

## SHADOW_MORE — EVM cross-venue and learned-route strategies
A quoted positive route can still have negative expected value when approval/setup costs, revert probability, replacement transactions, nonce contention, builder payments, and non-atomic leg risk are included. Economics also vary materially among EVM chains.

## RESEARCH_MORE — Strategy promotion governance
Promotion should require independent forward outcomes with complete cost and failure attribution, not AI agreement, raw confidence, quote simulation, or a handful of winning trades.
