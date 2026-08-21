# GPT strategy review

Architecture-only review completed. Both chains contain useful fail-closed protections and money-weighted accounting elements, but profitability, CANARY readiness and LIVE readiness cannot be established without fresh runtime forensics. The principal strategic gap is EVM SiBot entry: it bounds round-trip friction but does not require a positive executable follower edge after entry/exit gas, price impact, slippage and platform fees. Its broad exception suppression also prevents reliable separation of STRATEGY/MARKET losses from EXECUTION/INFRASTRUCTURE failures. Solana has materially stronger chain-specific quote, impact, fee, rent, simulation and realised-P&L controls; these should be retained and evaluated out of sample rather than loosened.

## NEW_SHADOW — EVM SiBot leader-copy entry
A leader's historical profitability and an acceptable friction ceiling do not prove that the delayed follower entry has positive money-weighted edge. A SHADOW-only admission model should reject unless conservative expected follower proceeds exceed principal plus both legs of gas, DEX fees, price impact/slippage, platform fees and model-error reserve.

## IMPROVE — Cross-chain strategy and execution attribution
Durable strategy evaluation requires STRATEGY/MARKET loss to be separated from EXECUTION/INFRASTRUCTURE failure. Otherwise execution outages can make a good signal appear bad, while selection bias from failed losing orders can make a strategy appear good.

## SHADOW_MORE — Solana leader-copy positive-edge stack
The Solana architecture accounts for chain-specific priority fees, price impact, slippage, rent and transaction failure more comprehensively than the EVM follower path. With runtime evidence absent, the correct action is to retain protections and collect falsifiable SHADOW evidence rather than infer profitability or weaken thresholds.
