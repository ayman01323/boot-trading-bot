# GPT strategy review

Architecture-only review completed. The repository contains substantial fail-closed execution protection and distinguishes execution-failure counts from realised trading results, but Strategy Lab and SHADOW records do not yet provide a complete, chain-normalised, money-weighted net-P&L attribution covering every executable cost and failure. Solana has explicit impact, slippage, priority-fee, platform-fee and refundable-rent handling; EVM has fresh simulation, gas reserves and receipt gas accounting. No profitability, CANARY-readiness or LIVE-readiness conclusion is permitted because current runtime forensics are unavailable.

## REWORK — Strategy Lab performance attribution
Durable selection requires comparable money-weighted NET P&L and explicit causal attribution. A negative executable trade should be STRATEGY/MARKET loss; quote, simulation, RPC, signing, broadcast, landing or reconciliation faults should be EXECUTION/INFRASTRUCTURE failures, including any gas lost. Collapsing these dimensions can replace sound signals because of infrastructure faults or retain weak signals whose costs are incomplete.

## SHADOW_MORE — Cross-chain signal families
A single expected-edge number is insufficient across Solana and EVM. Solana needs compute/priority or Jito costs, account-rent treatment, Jupiter platform fees, route impact and landing reliability. EVM needs EIP-1559/legacy gas economics, approval/preparation costs, reverts, nonce/replacement failures, MEV exposure and receipt-confirmed gas.

## KEEP — Positive executable-edge and sellability gates
These controls align with the objective: do not manufacture trades, do not weaken liquidity or sellability protection, and require executable edge after chain-specific friction.
