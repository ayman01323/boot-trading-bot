# GPT strategy review

Architecture-only review completed. The shared SHADOW framework correctly requires positive cost-adjusted edge and prevents simulated quotes from becoming promotion evidence. Solana has stronger chain-specific entry economics, including round-trip deterioration, network and priority fees, slippage, latency, impact and refundable-rent treatment. The EVM SiBot copy-entry path is weaker: its round-trip-loss ceiling can admit a negative-edge trade and does not deduct estimated buy and exit gas from an explicit executable-edge requirement. It also silently discards broad exceptions, obstructing separation of STRATEGY/MARKET losses from EXECUTION/INFRASTRUCTURE failures. Runtime forensics are absent, so no profitability, CANARY-readiness or LIVE-readiness claim is made.

## KEEP — Cross-chain Strategy Lab evaluators
These are durable governance properties aligned with net P&L and the rule that no trade is forced without executable edge.

## REWORK — EVM SiBot leader-copy entry
A maximum tolerable round-trip loss is a safety limit, not evidence of positive executable edge. Copying should require a conservative expected follower return to exceed buy gas, exit gas, DEX fees, impact, slippage, latency and failure-cost reserves.

## SHADOW_MORE — Solana leader-copy and common Strategy Lab signals
Solana safeguards are economically thoughtful, but the common SHADOW evidence pipeline cannot presently evaluate realised or counterfactual outcomes. Historical leader returns are not proof of current follower edge.
