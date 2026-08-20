# GPT strategy review

Architecture-only review completed. EVM cyclic execution has useful fail-closed quoting, gas estimation, atomic minimum-output protection and final eth_call simulation. Solana shadow copying checks quote round trips and entry deterioration. However, promotion accounting can accept caller-supplied net P&L without reconciling costs, does not adequately separate strategy/market losses from execution/infrastructure failures, and Solana shadow costs rely on static fee assumptions. Learned-route discovery also selects only historically positive observations, creating survivor bias. No profitability, canary-readiness or live-readiness conclusion is permitted without fresh runtime forensics.

## REWORK — Strategy Lab lifecycle evaluation
A durable money-weighted decision requires independently reconcilable all-in net returns. Strategy or market losses must be recorded separately from failed simulation, RPC, signing, landing, revert, timeout and accounting failures; otherwise infrastructure defects can incorrectly retire a sound hypothesis or omitted costs can promote a weak one.

## SHADOW_MORE — Solana leader-copy shadow strategy
Solana priority fees, route price impact, account creation/rent effects, quote expiry and landing failures vary by transaction and congestion. Static fees can overstate executable edge, particularly for small allocations. Quote-derived strategy loss must remain distinct from transaction construction, simulation, RPC, blockhash and landing failures.

## IMPROVE — EVM atomic cyclic arbitrage
The atomic route protects cycle output, but durable user net must include all economically attributable transactions and failed-attempt gas. Ambiguous failure states also prevent diagnosing whether lost edge came from market movement or infrastructure.

## REWORK — Learned Route Replication
Positive-only historical selection can make unstable or losing route families look repeatable. Fresh executable quoting remains necessary, but candidate prioritisation should use all comparable positive and negative outcomes plus failure costs.
