# GPT strategy review

Architecture-only review completed. The repository correctly defaults new hypotheses to SHADOW, requires positive estimated executable edge, and avoids treating raw win count as the objective. However, current SHADOW economics are not reliable promotion evidence: Solana has no current executable-edge adapter; EVM scanner slippage reserves are calculated from gross profit rather than notional; quoted route output appears already impact-inclusive while the common feature model subtracts price impact again; and Strategy Lab promotion metrics do not require explicit monetary attribution for failed executions, gas/priority fees, or all-in realised outcomes. These are strategy-evidence/accounting defects, not proof of market losses or execution failures. No profitability, CANARY-readiness, or LIVE-readiness claim is supported.

## REWORK — Cross Venue Net Arbitrage and all common-feature strategies
SHADOW comparisons need one documented economic basis per component. Impact already embedded in executable quoted output must not be subtracted again, while adverse-fill reserve must be based on notional or a leg-aware worst-case amount rather than only expected profit. Until normalized, eligible-signal counts and expected net edge may be materially distorted in either direction.

## NEW_SHADOW — Solana Current Executable Edge
Historical leader win ratios cannot establish forward executable edge. Solana needs a SHADOW-only observation joining decision-time Jupiter entry and reverse-exit quotes, platform fees, price impact, slippage reserve, priority/Jito fees, rent/account costs where applicable, latency reserve, sellability, simulation, and subsequent marked or realised outcome.

## IMPROVE — Strategy Lab lifecycle evaluation
Durable money-weighted NET P&L requires every attempted trade to contribute capital-weighted realised or conservative marked P&L, including gas/priority fees, failed-attempt costs, price impact, slippage, platform fees, and stranded exposure. Failure classification must distinguish STRATEGY/MARKET losses from EXECUTION/INFRASTRUCTURE failures without excluding either from economic P&L.

## SHADOW_MORE — Common cross-chain strategy evaluators
Shared strategy logic is useful, but Solana priority fees, quote lifetime, account/rent effects, and inclusion behavior differ from EVM gas, nonce, mempool, and block-inclusion economics. Fixed universal thresholds are hypotheses, not validated economics.
