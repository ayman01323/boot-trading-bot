# GPT strategy review

Architecture-only review completed. The repository generally fails closed and correctly prevents SHADOW quote/simulation results from becoming promotion evidence. However, durable money-weighted NET P&L cannot be assessed: current runtime forensics are absent, Solana observations lack executable forward-edge features, and SHADOW accounting does not attribute failed-execution costs. EVM discovery also emits zero gas and builder costs until later simulation, while the shared model applies fixed basis-point thresholds across materially different chain economics. STRATEGY/MARKET losses must be measured separately from EXECUTION/INFRASTRUCTURE failures before any profitability, CANARY, or LIVE claim.

## IMPROVE — Cross-chain SHADOW outcome accounting
Measure each timestamped decision as money-weighted realised or counterfactual NET P&L, including fees lost on failed transactions. Classify adverse results as STRATEGY_MARKET when execution succeeded but the post-decision economics lost money, and EXECUTION_INFRASTRUCTURE when quoting, simulation, RPC, submission, landing, validation, or reconciliation failed. Failed attempts must not disappear from strategy economics.

## REWORK — Executable-edge cost model
EVM edge must include current gas, builder/priority payment, approval or setup costs, revert probability times gas-at-risk, transfer-tax behavior, and inclusion latency. Solana edge must include base and priority fees, Jito tips, platform fees, account creation, non-refunded rent, landing probability, and stale-blockhash or RPC failure drag. Cost definitions should state whether quote impact is already embedded.

## NEW_SHADOW — Solana executable forward-edge adapter
Historical leader profitability is not executable follower edge. A SHADOW-only adapter should capture contemporaneous entry and unwind quotes at the follower's notional, route-level impact, priority/platform fees, rent exposure, latency reserve, sellability, and landing probability while preserving abstention when any component is missing.

## KEEP — Fail-closed execution and promotion controls
These controls enforce abstention and separate research evidence from execution authority. They should remain unchanged while runtime profitability and failure-cost evidence are absent.
