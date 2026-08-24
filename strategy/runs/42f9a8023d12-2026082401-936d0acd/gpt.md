# GPT strategy review

Architecture-only review completed. The repository generally preserves SHADOW/LIVE separation and positive-edge safety gates, but promotion evidence and cost attribution are not sufficiently robust for durable money-weighted net-P&L conclusions. EVM scanner economics understate slippage and omit gas during candidate ranking; Strategy Lab can mix realised and expected results while failing to record execution failures; Solana entry economics use static expected-margin and historical-return proxies rather than a fully executable per-trade net-edge forecast. These are architecture findings, not evidence of current losses or profitability. Missing runtime forensics prohibit profitability, CANARY-readiness and LIVE-readiness claims.

## REWORK — Strategy Lab evaluation and promotion evidence
Promotion must be based on realised, chain-specific, money-weighted results after every cost. Expected outcomes and broadcasts are not realised P&L. Failed attempts must be attributed separately so EXECUTION/INFRASTRUCTURE failures do not masquerade as STRATEGY/MARKET losses or disappear from evaluation.

## IMPROVE — EVM direct-market triangular arbitrage
EVM economics must reserve multi-leg slippage/price impact against traded notionals and use chain-specific gas, including failed-transaction exposure. The current downstream simulation protects execution, but preliminary ranking is not a reliable executable-net-edge ranking.

## SHADOW_MORE — Solana leader-copy positive-edge selection
A leader can be historically profitable while follower latency, route impact, priority fees and exit liquidity eliminate executable edge. Strategy loss must be evaluated after these costs; quote/API/RPC failures and inclusion failures must remain EXECUTION/INFRASTRUCTURE classifications.
