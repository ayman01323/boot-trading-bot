# GPT strategy review

Architecture-only review completed. Both chains contain fail-closed execution protections, but no fresh runtime evidence supports profitability or CANARY/LIVE readiness. Solana SHADOW research lacks contemporaneous executable quote economics. EVM SHADOW observations can contain exact quotes and simulations but lack future realised outcomes. Current forensics report losses and execution attempts without a sufficiently explicit STRATEGY/MARKET versus EXECUTION/INFRASTRUCTURE attribution. Preserve abstention and protections while improving SHADOW outcome measurement and failure attribution.

## IMPROVE — Cross-chain Strategy Lab scorecards
Strategy selection needs delayed, out-of-sample, money-weighted NET outcomes rather than eligibility counts or contemporaneous simulations. Outcomes must include DEX fees, slippage, price impact, EVM gas/priority fees, Solana base/priority fees and rent effects, plus costs of failed attempts.

## NEW_SHADOW — Solana executable-edge adapter
Historical leader return cannot establish the follower's current executable edge. Solana needs decision-time round-trip quote economics that incorporate route price impact, slippage, base and priority fees, token-account/rent effects, latency decay, sellability and simulation success.

## IMPROVE — Cross-chain loss forensics
A negative realised trade after valid execution is a STRATEGY/MARKET loss; reverts, dropped/expired transactions, RPC faults, stale blockhashes, confirmation ambiguity and accounting gaps are EXECUTION/INFRASTRUCTURE failures. These require different responses and must not be blended when judging edge.

## SHADOW_MORE — EVM atomic net arbitrage
The architecture correctly favors atomic execution and positive executable edge, but quote/simulation safeguards do not prove capture after latency, priority bidding, competition and failures.
