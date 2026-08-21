# GPT strategy review

Architecture-only review completed. The repository contains useful fail-closed execution controls, but no fresh runtime forensics exist, so profitability and CANARY/LIVE readiness cannot be assessed. EVM routes account for quoted gas, builder fees, slippage, price impact and latency reserves, while Solana live execution separately guards priority fees, platform fees, impact, slippage and rent. The largest strategy-evaluation weakness is that quote-derived expected edge is recorded as an available outcome, without delayed executable outcomes or explicit STRATEGY/MARKET versus EXECUTION/INFRASTRUCTURE attribution. Solana Strategy Lab observations deliberately have no current executable edge adapter. Prefer abstention and SHADOW measurement until these gaps are closed.

## REWORK — Cross-chain Strategy Lab outcome measurement
A contemporaneous quote is not a future outcome and cannot measure adverse selection, quote decay, failed inclusion, revert probability or exit economics. Add delayed, immutable SHADOW outcomes at fixed horizons and classify negative results as STRATEGY/MARKET when executable prices moved against the signal, or EXECUTION/INFRASTRUCTURE when quoting, simulation, RPC, inclusion or settlement failed. Keep unclassifiable cases separate rather than treating failures as market losses or omitting them.

## NEW_SHADOW — Solana current executable edge adapter
Historical leader profitability cannot establish follower edge after copy latency and Solana-specific execution costs. A non-signing adapter should measure current entry plus executable unwind economics and retain rent as refundable capital exposure rather than permanent cost.

## SHADOW_MORE — Cross Venue Net Arbitrage and directional signal family
A nominal positive edge floor is insufficient unless it includes chain-, route-, notional- and regime-specific uncertainty plus expected failure cost. Arbitrage can use atomic protection, whereas momentum, mean reversion and copied flow also require round-trip exit costs and holding-period risk.

## KEEP — Executable-edge and sellability abstention gates
These controls support the objective of avoiding trades without demonstrable executable edge. They should remain intact while new SHADOW evidence is collected.
