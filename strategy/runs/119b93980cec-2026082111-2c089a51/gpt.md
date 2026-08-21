# GPT strategy review

Architecture-only review completed. EVM atomic-cycle execution has conservative gas, exact simulation and minimum-output protections, while Solana validates transactions and realised wallet economics. However, Solana directional entries currently depend substantially on historical leader returns rather than a contemporaneous executable round-trip edge, and the performance model does not fully attribute all failed-execution costs or consistently expose post-platform-fee user net P&L. No profitability, CANARY-readiness or LIVE-readiness conclusion is possible without fresh runtime forensics.

## NEW_SHADOW — Solana leader-copy current executable edge
A historically profitable leader can still be uncopyable after follower latency, price impact, Jupiter route costs, priority fees, account overhead and eventual exit slippage.

## SHADOW_MORE — EVM atomic single-router cyclic arbitrage
The design correctly targets atomic executable edge, but architecture cannot establish opportunity frequency, inclusion success, adverse selection, revert cost or durable net profitability.

## IMPROVE — Cost-complete strategy accounting
Durable money-weighted NET P&L requires every irreversible cost, including failed transaction fees and platform-fee settlement overhead, to be attributed to the responsible strategy and chain.

## IMPROVE — Strategy versus execution loss attribution
A negative completed trade is a STRATEGY/MARKET loss; RPC outage, simulation failure, stale blockhash, revert, dropped transaction, incomplete receipt or accounting mismatch is an EXECUTION/INFRASTRUCTURE event. Mixing them leads to incorrect strategy replacement or unsafe infrastructure promotion.
