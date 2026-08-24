# GPT strategy review

Architecture-only review completed. Solana has comparatively strong executable-quote, sellability, fee-aware P&L, realised-amount profit-factor and execution-validation controls. EVM leader history includes gas, but entry admission only caps deterioration and round-trip loss; it does not require the follower's expected return to exceed entry gas, exit gas, price impact, slippage and a safety margin. Strategy-pattern learning also computes average net from positive observations only, which can promote patterns with poor money-weighted outcomes. No profitability, CANARY-readiness or LIVE-readiness conclusion is permitted because current runtime forensics are absent. STRATEGY/MARKET losses must be measured from successfully executed positions with adverse net outcomes; quote, RPC, simulation, revert, missing-output, reconciliation and exit-submission faults must be reported separately as EXECUTION/INFRASTRUCTURE failures.

## REWORK — Cross-chain route-pattern learning
Positive-only averaging biases selection toward raw successes and can label a route attractive even when a few larger losses make aggregate NET P&L negative.

## NEW_SHADOW — EVM SiMo executable-edge admission
EVM gas and AMM impact vary materially by chain and trade size. A fixed loss ceiling is a safety filter, not proof of positive follower edge.

## IMPROVE — Cost-complete strategy attribution
Strategy selection becomes biased if reverted, timed-out, unreconciled or unexitable attempts disappear, or if infrastructure failures are counted as market losses. Conversely, a successfully executed adverse move is a STRATEGY/MARKET loss even when infrastructure operated correctly.

## KEEP — Solana guarded copy strategy
These controls are directionally aligned with executable NET P&L and should not be weakened. Architecture quality does not demonstrate actual profitability.
