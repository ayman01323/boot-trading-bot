# Gemini strategy review

Architecture review of cross-chain strategy signals and market feature adapters. EVM strategies parse baseline execution economics (fees, slippage, price impact) properly, although maxPriorityFeePerGas inclusions must be verified to distinguish execution failure from strategy loss. Solana strategies currently rely on leader events without executable quote adapters, resulting in fail-closed zero net edge evaluations. Runtime forensics are missing, preventing any live or canary promotion.

## IMPROVE — ALL
Stored leader events do not contain a contemporaneous executable future-return quote. The current architecture evaluates Solana signals to 0 edge, rendering all strategies fail-closed. A Solana-specific quote adapter is needed to generate real-time execution costs (including priority fees and compute units) and slippage to calculate a positive executable net edge.

## RESEARCH_MORE — ALL
EVM transaction costs are modelled using gas_base and builder_fee_base. In fast markets, priority fee spikes can drastically increase execution costs. We must verify if the incoming CSV opportunities fully encapsulate priority fees to prevent execution failure or margin erosion masquerading as strategy loss.
