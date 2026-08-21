# GPT strategy review

Architecture-only review completed. The repository has useful fail-closed quote, simulation, liquidity and sellability controls, and EVM atomic cycles reconcile receipt gas. However, SHADOW economics and promotion evaluation do not yet demonstrate durable money-weighted net P&L. EVM scanner slippage is reserved from gross profit rather than traded notional, Solana uses fixed fee estimates, and Strategy Lab promotion logic does not gate on execution-failure rate or explicitly separate strategy/market losses from infrastructure failures. No profitability, CANARY-readiness or LIVE-readiness claim is supported.

## IMPROVE — Cross-chain Strategy Lab evaluation
A losing executable trade is strategy/market evidence; a revert, timeout, RPC error, stale quote or unavailable exit is execution/infrastructure evidence. Mixing them prevents correct strategy selection and understates the economic cost of failed attempts.

## REWORK — EVM Cross Venue Net Arbitrage and Learned Route Replication
Applying slippage bps to a small gross edge can materially under-reserve adverse execution cost. The atomic wallet simulation is a strong final guard, but SHADOW ranking and candidate selection can still be distorted.

## SHADOW_MORE — Solana Leader Copy and New Liquidity Quality
Solana priority fees, account creation/rent, route price impact, quote latency and failed-transaction costs vary materially. Fixed fee assumptions cannot establish durable net profitability, especially for 0.05 SOL notionals.

## DORMANT — Liquidity Confirmed Momentum, Dislocation Mean Reversion, Flow Acceleration and Forecasted Positive Net Edge
The hypotheses are reasonable research candidates, but there is no fresh runtime evidence showing calibrated future returns after chain-specific costs. They should not compete for promotion based on heuristic scores or raw win count.
