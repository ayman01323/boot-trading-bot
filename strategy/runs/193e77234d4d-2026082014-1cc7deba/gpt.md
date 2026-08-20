# GPT strategy review

Architecture-only review completed and evidence hash/commit verified. The repository contains strong fail-closed execution concepts and correctly avoids treating quote simulations as realised profit, but Strategy Lab accounting can aggregate unlike native currencies, accepts externally supplied net profit, omits explicit price-impact/gas/priority/rent fields, and does not economically attribute execution failures. Solana SHADOW inputs lack current executable quote-edge evidence. Consequently no profitability, CANARY-readiness or LIVE-readiness claim is supported. STRATEGY/MARKET losses must be measured separately from EXECUTION/INFRASTRUCTURE costs and failures.

## REWORK — Strategy Lab portfolio evaluation
Summing SOL, ETH and other native units is economically invalid, while caller-supplied net values can conceal omitted costs. Record immutable cost components and settlement denomination per observation. Classify adverse outcomes as STRATEGY_MARKET when an executed position loses after complete costs, EXECUTION when execution quality or transaction failure consumes edge, and INFRASTRUCTURE when RPC/indexer/queue failure prevents or corrupts measurement.

## NEW_SHADOW — Solana executable-edge adapter
Historical leader quality is not current executable edge. Solana requires contemporaneous round-trip quotes, size-dependent impact, base and priority fees, account/rent treatment, sellability, quote age, landing probability and failed-attempt cost.

## SHADOW_MORE — Cross-chain strategy families
A positive aggregate can conceal chain, size or regime dependence. EVM must include gas bidding, builder payments, revert/replacement costs and atomicity; Solana must include priority fees, landing failures, account economics and non-atomic exit risk. Promotion evidence should be forward, chain-stratified and cost-complete.
