# GPT strategy review

Architecture-only review completed. The repository correctly prefers abstention and contains substantial EVM and Solana execution protections, but no current runtime evidence supports profitability or CANARY/LIVE readiness. Research accounting can overstate durable user net P&L by omitting profit-share settlement and failed-transaction costs, and generic scorecards lack safe cross-chain money weighting. Strategy/market losses and execution/infrastructure failures are counted separately in places, but their monetary costs are not consistently reconciled. Keep all strategies SHADOW until chain-specific, out-of-sample realised evidence proves positive executable net P&L.

## REWORK — Strategy promotion and canary lifecycle
Architecture approval is not economic evidence. Promotion must require fresh runtime forensics, independently reconciled realised outcomes, adequate regime coverage, and exact-source matching.

## IMPROVE — Direct Market Arbitrage and Strategy Lab accounting
The objective is user NET P&L. Expected results, pending broadcasts, profit-share liabilities, failed gas/priority spend, and unresolved rent cannot be treated as realised profit.

## REWORK — Cross-chain Strategy Lab scorecards
SOL, ETH, BNB, and other native-token amounts are not additive. Durable performance needs per-chain cost models and a common valuation unit captured at decision and outcome time, alongside deployed capital.

## NEW_SHADOW — Solana chain-specific signal validation
The shared strategy families cannot currently be evaluated economically on Solana. Historical leader win ratios are not executable follower edge and must remain contextual only.

## KEEP — Common executable-edge and safety gates
Abstention is economically correct when executable edge is absent. These protections align with net-P&L optimization and must not be weakened to increase win count or activity.
