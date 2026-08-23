# GPT strategy review

Architecture-only review completed. The common SHADOW evaluators correctly require positive cost-adjusted edge and fail Solana observations closed when current executable edge is unavailable. However, production leader-copy admission remains substantially historical rather than contemporaneously executable, Solana fee budgeting uses a configured expected-profit margin, and EVM Strategy Lab accounting can treat BROADCAST or expected P&L as realised performance. These issues can confound durable money-weighted NET P&L assessment. STRATEGY/MARKET loss means a confirmed, reconciled round trip executed as intended but produced negative net P&L after all costs. EXECUTION/INFRASTRUCTURE failure means rejection, revert, dropped or unreconciled broadcast, material quote-to-fill deterioration, stale detection, RPC/API failure, or missing balance reconciliation. No fresh runtime forensics exist, so profitability and CANARY/LIVE readiness are not established.

## REWORK — Direct Market Arbitrage
A broadcast is not a confirmed, reconciled economic outcome, and expected profit is not realised profit. Mixing either into LIVE windows can overstate net P&L and misclassify execution failures as strategy outcomes.

## SHADOW_MORE — SiBot EVM Leader Copy
A historically profitable leader can cease to offer positive follower edge after detection delay, copied-size impact, gas and exit costs. Chain-specific EVM economics also differ materially between Ethereum and lower-fee chains.

## NEW_SHADOW — Solana Leader Copy Executable Edge
Solana architecture honestly abstains in Strategy Lab, but there is no demonstrated signal-specific executable edge covering round-trip Jupiter economics, priority fees, account rent effects, price impact, copy delay and failed attempts.

## IMPROVE — Cross-chain Strategy Evaluation
Strategy thresholds should not be changed in response to RPC, quote, simulation, inclusion or reconciliation failures, while confirmed adverse market outcomes must not be excused as infrastructure.
