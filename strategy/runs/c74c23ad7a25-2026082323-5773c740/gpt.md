# GPT strategy review

Architecture-only review completed. Both engines contain fail-closed executability protections, but the Strategy Lab evidence model does not consistently preserve every cost or execution failure and cannot safely aggregate unnormalised native-token results across chains. Solana leader-copy has a current-quote executable-edge gate, whereas its expected move and fixed latency/fee reserves remain hypotheses requiring SHADOW calibration. EVM direct-market execution re-simulates per wallet and records realised gas-adjusted results, but rejected executions are excluded from strategy evaluation. STRATEGY/MARKET losses must be measured from successfully executable trades with negative net outcomes; quote, simulation, broadcast, inclusion, reconciliation, or exit failures must be separately attributed as EXECUTION/INFRASTRUCTURE failures. No profitability, CANARY-readiness, or LIVE-readiness conclusion is possible from the supplied architecture-only evidence.

## REWORK — Strategy Lab cross-chain evaluation
Lifecycle decisions should use durable money-weighted net P&L and explicitly separate signal economics from execution reliability. Current recording can overstate strategy quality or obscure infrastructure failures.

## SHADOW_MORE — Solana SiBot leader copy
The architecture correctly demands positive executable edge, but fixed reserves and leader-history forecasts require calibration against follower outcomes, especially for fast Solana markets where copy delay can consume the signal.

## IMPROVE — EVM direct-market arbitrage
EVM chain economics differ materially. Candidate ranking and evaluation should optimise expected net value per unit of capital and time after gas and failure probability, not only gross opportunity size.
