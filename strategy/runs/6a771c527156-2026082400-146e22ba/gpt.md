# GPT strategy review

COPILOT_HANDOFF_ACK: 2026-08-21T21-52Z-copilot-protocol. Architecture correctly defaults to abstention and separates SHADOW research from LIVE execution, but its evaluation ledger does not yet prove durable money-weighted net profitability. Solana Strategy Lab inputs lack contemporaneous executable edge entirely. EVM inputs model chain-specific gas and builder costs, but quote simulations are not realised outcomes. Recorded Strategy Lab net profit can omit price impact, gas/priority fees and costs from failed attempts. Consequently no strategy is claimed profitable, CANARY-ready or LIVE-ready. Observed losses cannot presently be apportioned reliably between STRATEGY/MARKET loss and EXECUTION/INFRASTRUCTURE failure.

## IMPROVE — Strategy Lab money-weighted evaluation
Promotion decisions should use one auditable money-weighted ledger containing actual Solana base/priority fees, EVM gas and builder payments, realised slippage/price impact, and fees burned by reverted, expired or otherwise failed attempts. Failure costs are EXECUTION/INFRASTRUCTURE losses; adverse post-fill price movement is STRATEGY/MARKET loss.

## NEW_SHADOW — Solana contemporaneous executable-edge research
Solana cannot be compared with EVM or evaluated as a strategy while its Strategy Lab adapter records only leader events without current executable entry/exit economics. Historical leader returns describe a signal hypothesis, not executable follower returns.

## REWORK — Cross-chain SHADOW outcome and failure attribution
A current positive quote is an eligibility check, not an outcome. Durable evaluation requires pre-registered, forward, out-of-sample resolution and explicit attribution: adverse market movement or invalid signal is STRATEGY/MARKET; quote, RPC, simulation, submission, inclusion, reconciliation or exit-path malfunction is EXECUTION/INFRASTRUCTURE.

## SHADOW_MORE — EVM exact-quote route strategies
The EVM architecture has appropriate pre-trade gates, but no fresh realised evidence establishes calibration, inclusion probability, revert cost, latency deterioration or durable net edge by chain and route.
