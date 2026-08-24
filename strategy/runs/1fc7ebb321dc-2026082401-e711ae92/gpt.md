# GPT strategy review

Architecture-only review completed. Existing evaluators require positive estimated net edge and fail closed for missing Solana edge, but the Strategy Lab has no realised out-of-sample outcome layer and its EVM cost inputs can be absent or zero. Consequently no strategy is proven profitable, canary-ready, or live-ready. STRATEGY/MARKET loss means a correctly executed trade produced negative fully loaded net P&L; EXECUTION/INFRASTRUCTURE failure means the intended trade was not obtained or reconciled because of quote expiry, rejection, revert, RPC/API failure, latency, or fill mismatch. Current evidence cannot quantify either class.

## REWORK — Strategy Lab outcome measurement
Expected quote profit cannot establish durable money-weighted net P&L or measure adverse selection and execution failure. Add a non-signing forward-outcome ledger that freezes decision-time inputs and later records executable unwind value, all fees, slippage, price impact, and failure classification.

## IMPROVE — EVM positive executable edge
EVM edge must include DEX fees, actual gas and priority pricing, approval/setup costs when applicable, builder payments, slippage, price impact, quote decay, platform fees, and expected failed-attempt cost. Missing mandatory components should make the SHADOW observation economically unresolved.

## NEW_SHADOW — Solana leader-following executable edge
A profitable leader can be unprofitable to copy after detection delay, Jupiter route impact, priority fees, slippage, rent/account lifecycle effects, and failed attempts. Build only a SHADOW adapter using contemporaneous entry and executable unwind quotes; historical leader win statistics remain context, not edge.

## IMPROVE — Cross-chain loss forensics
Strategy changes should respond to correctly executed negative expectancy, while infrastructure changes should respond to failed or degraded execution. Stable mutually exclusive primary classes with secondary causes are needed before adjudicating either.
