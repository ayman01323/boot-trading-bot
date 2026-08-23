# GPT strategy review

Architecture-only review completed. The repository correctly prioritises cost-adjusted edge, fail-closed execution checks and money-weighted results over raw wins. EVM execution includes fresh wallet-specific simulation, gas-relative profit floors and receipt-confirmed realised P&L. Solana safety and realised-amount gates are substantial, but its Strategy Lab adapter deliberately lacks contemporaneous executable edge, making Solana profitability untestable in the supplied evidence. Promotion governance also needs stronger out-of-sample enforcement and explicit separation of strategy/market losses from execution/infrastructure failures. No profitability, CANARY-readiness or LIVE-readiness conclusion is supported.

## IMPROVE — Solana leader-copy and cross-chain Strategy Lab families
Historical leader returns cannot establish follower edge after Jupiter price impact, priority/network fees, copy delay and likely exit costs. This is a STRATEGY/MARKET evidence gap, not proof of an infrastructure failure.

## REWORK — Strategy Lab lifecycle evaluation
Eight aggregated observations can be dominated by one regime, chain or large winner. Durable money-weighted net P&L requires explicit chronological holdout evidence, cost completeness and downside stability.

## IMPROVE — Strategy Lab attribution and replacement decisions
A sound signal that fails because of quote expiry, RPC failure, transaction construction, revert or inclusion delay should not be classified identically to a filled trade whose market move produced a loss. Conversely, infrastructure explanations must not excuse filled negative-edge trades.

## KEEP — EVM atomic cycle execution
The architecture correctly treats gas and execution verification as part of edge and records receipt-confirmed money-weighted outcomes. This does not prove the strategy profitable without fresh runtime distributions.
