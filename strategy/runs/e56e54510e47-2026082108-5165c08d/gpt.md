# GPT strategy review

Architecture-only review completed. The repository generally fails closed on missing executable edge and contains chain-specific safeguards, but no fresh runtime evidence supports profitability or promotion. Solana Strategy Lab deliberately lacks a current executable-edge adapter, while EVM shadow evidence is quote/simulation-only. Strategy/market losses must be measured from completed, cost-inclusive outcomes; RPC, simulation, landing, reconciliation, receipt and settlement failures must remain separately classified as execution/infrastructure failures. No strategy is canary-ready or live-ready from this evidence.

## KEEP — Cross-chain positive executable edge gating
Abstention is economically preferable to forcing trades when executable net edge is unproven. These controls also prevent infrastructure faults from being mislabeled as strategy losses.

## NEW_SHADOW — Solana cost-complete executable counterfactual
Historical leader returns are strategy/market evidence, not proof that a copied trade remains profitable after copy latency, Jupiter route impact, priority fees, account creation and exit costs.

## SHADOW_MORE — EVM atomic route strategies
Quote wins are not durable money-weighted profit. Evaluation needs the full attempted-opportunity denominator, actual gas and platform fees, and explicit separation between market losses and infrastructure failures.
