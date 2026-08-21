# GPT strategy review

Architecture-only review completed. The EVM path has useful fail-closed quote, simulation, liquidity, sellability and receipt-confirmed gas accounting, while the common signal layer requires positive cost-adjusted edge. However, the Solana Strategy Lab adapter deliberately supplies zero current edge and no executable outcome, so Solana strategies cannot yet be economically evaluated there. Strategy Lab accounting also lacks explicit price-impact, gas/priority-fee, rent-state and execution-failure attribution fields, creating a risk that incomplete caller-provided net values or infrastructure failures are interpreted as strategy performance. No profitability, CANARY-readiness or LIVE-readiness claim is supported without fresh runtime forensics and independent realised outcomes.

## REWORK — Solana Strategy Lab feature and outcome adapter
A historical leader success ratio is not an executable edge. Solana needs timestamped entry and exit quotes at the tested size, route price impact, DEX/platform fees, base and priority/Jito fees, latency/adverse-selection reserve, sellability, liquidity, simulation result, and refundable-rent state before a strategy signal can be evaluated. Rent principal should not be treated as permanent loss while recoverable, but failed or unrecovered rent must remain visible.

## IMPROVE — Money-weighted Strategy Lab accounting
Strategy/market loss means an economically valid execution or mark-to-executable-exit produced negative net performance. Execution/infrastructure failure means quote, RPC, simulation, signing, broadcast, confirmation, reconciliation or settlement failed. These categories have different remedies, but both can consume money and must enter failure-adjusted net P&L where applicable.

## SHADOW_MORE — Cross Venue Net Arbitrage
This is the best-supported architectural hypothesis because the cycle is atomic and the current path applies several executable safeguards. Fixed basis-point thresholds still cannot establish edge across EVM chains with materially different gas and inclusion economics, and no fresh realised evidence is available.
