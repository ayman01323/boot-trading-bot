# GPT strategy review

Architecture-only review completed. Safety gates generally fail closed, but profitability and promotion claims are prohibited because runtime forensics are absent. EVM opportunity ranking materially understates executable costs: scanner gas is zero and slippage reserve is calculated from gross profit rather than deployed capital. Solana has a cost-adjusted entry gate, but its leader-return estimator uses only means over as few as five observations and needs out-of-sample, money-weighted validation. Loss forensics also use overlapping heuristic flags rather than an exclusive STRATEGY/MARKET versus EXECUTION/INFRASTRUCTURE attribution.

## REWORK — EVM V2/V3 single-router triangular arbitrage
Positive quoted gross edge is not positive executable edge. EVM gas, per-hop adverse fill, transfer-tax behavior, quote decay and failed-transaction gas must be charged using chain-specific values before a candidate is ranked.

## SHADOW_MORE — Solana confirmed-fast-lane leader following
The entry gate is directionally sound but small-sample means are sensitive to skew and leader selection bias. Strategy/market alpha must be validated separately from Jupiter, RPC, signing, simulation and inclusion failures.

## IMPROVE — Cross-chain strategy evaluation and abstention
Strategy changes cannot be evaluated correctly when market losses and infrastructure failures are pooled. Each attempted trade needs one primary cause and optional contributing causes, with failed-attempt costs included in strategy-level money-weighted net P&L.
