# GPT strategy review

Architecture-only review completed. The repository contains useful fail-closed simulation, liquidity, sellability and cost controls, but no fresh runtime forensics support profitability, CANARY readiness or LIVE readiness. Solana leader selection relies materially on historical returns rather than a contemporaneous executable round-trip edge, while EVM and shared SHADOW accounting need fuller attribution of reverted transactions, approvals, priority fees, price impact and other execution leakage. Strategy/market losses must be evaluated separately from execution/infrastructure failures.

## IMPROVE — All Strategy Lab strategies
Money-weighted evaluation is unreliable unless every attempt is attributed as STRATEGY_MARKET_LOSS, EXECUTION_INFRA_FAILURE, SUCCESS or NO_TRADE and all irreversible costs are charged. Failed EVM transactions can consume gas; approvals add gas; Solana attempts can consume base fees, priority fees, tips or rent-related cash movements. These must not be hidden inside trade losses or omitted.

## REWORK — Solana leader-copy entry
A profitable leader history does not establish follower edge after copy latency, entry impact, eventual exit impact, base and priority fees, rent effects and failed attempts. Solana needs a SHADOW-only contemporaneous entry-and-exit cost envelope and must abstain when the exit distribution cannot support positive expected net value.

## SHADOW_MORE — EVM atomic cycle and learned-route strategies
EVM preflight is sound protection but cannot prove realised edge under state competition, base-fee movement, nonce/RPC failures, reverted gas, approval costs or builder leakage. These are EXECUTION/INFRASTRUCTURE costs and must remain distinct from a successfully executed route whose market economics lose.

## KEEP — Cross-chain Strategy Lab governance
These controls align with abstention when executable edge is absent and prevent raw win count from becoming the objective. They should remain while outcome accounting and runtime evidence are strengthened.
