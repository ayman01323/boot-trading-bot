# GPT strategy review

Architecture-only review completed. The repository correctly emphasizes money-weighted net economics, abstention without executable edge, and fail-closed SHADOW evaluation. However, no current runtime forensics exist, so no profitability, CANARY-readiness, or LIVE-readiness conclusion is permitted. No observed loss can presently be classified. Architecturally, negative realised outcomes after successful execution belong to STRATEGY/MARKET, while quote, simulation, submission, confirmation, RPC, balance-reconciliation, or sell failures belong to EXECUTION/INFRASTRUCTURE and must not be counted as strategy losses. EVM SHADOW evidence is largely quote/simulation-only and Solana's common adapter deliberately supplies no executable edge. Cost attribution and out-of-sample outcome linkage therefore require improvement before promotion decisions are reliable.

## IMPROVE — EVM strategy families
The current SHADOW scorecard cannot reliably connect a decision-time strategy signal to wallet-specific gas simulation and subsequent realised net outcome. This prevents unbiased evaluation of executable EVM edge and obscures selection loss versus execution failure.

## NEW_SHADOW — Solana leader-copy and market-native signals
Solana cannot pass the common positive-edge evaluator because the common adapter has no executable quote economics. Historical leader win ratio is context, not current edge. A dedicated SHADOW adapter should measure decision-time Jupiter entry and exit liquidity, route price impact, priority/network fees, token-account rent effects, sellability, and delayed outcomes without enabling execution.

## REWORK — Cross Venue Net Arbitrage
A size-specific executable quote normally already reflects pool price impact. Subtracting a separately supplied price-impact value may double-count it unless gross_edge_bps is explicitly defined against an impact-free reference. Conversely, missing priority, failed-attempt, or platform-settlement costs can overstate edge. The accounting contract is currently ambiguous.

## IMPROVE — Strategy Lab lifecycle evaluation
Native SOL, ETH, BNB, and other chain units cannot be added directly. Execution failures also require their realised fees and opportunity effects without being mislabeled as adverse market expectancy. Promotion thresholds of eight trades and three windows are insufficient for heterogeneous multi-chain evidence.
