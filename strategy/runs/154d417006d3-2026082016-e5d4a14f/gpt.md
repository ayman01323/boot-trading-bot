# GPT strategy review

Architecture-only review completed. The repository contains useful fail-closed simulation, liquidity, sellability and cost controls, but current Strategy Lab accounting cannot establish durable money-weighted net profitability. Cross-chain native amounts lack a common valuation basis, shadow net results are derived from modeled signal edge rather than subsequent executable outcomes, and execution failures are not classified separately from strategy/market losses. No profitability, CANARY-readiness or LIVE-readiness claim is supported.

## REWORK — Strategy Lab portfolio evaluation
SOL, ETH and other EVM native-unit amounts are not economically additive. Raw aggregation can reverse rankings and is not money-weighted. Evaluation also omits capital-at-risk duration and explicit price-impact, priority-fee and failed-transaction costs.

## SHADOW_MORE — Cross-chain shadow strategy families
A contemporaneous modeled edge is not an outcome and risks circular validation. Strategies need immutable signal-time quotes followed by independently sampled executable exits or route completions, including failed and expired attempts.

## IMPROVE — Strategy and execution attribution
STRATEGY/MARKET losses are valid executions whose prices moved adversely or whose edge decayed. EXECUTION/INFRASTRUCTURE failures include quote expiry, simulation/RPC failure, rejection, dropped transaction, reverted transaction, missing confirmation and landed-but-unreconciled results. Combining these prevents correct strategy diagnosis while potentially omitting paid failure costs.

## NEW_SHADOW — Prospective executable-edge calibration
EVM edge should cover route fees, approval/amortization costs, EIP-1559 or legacy gas, MEV or builder payments, price impact and revert risk. Solana edge should cover Jupiter route economics, priority and base fees, account creation or refundable rent treatment, price impact, quote decay and landing risk. Historical leader performance cannot substitute for current executable edge.
