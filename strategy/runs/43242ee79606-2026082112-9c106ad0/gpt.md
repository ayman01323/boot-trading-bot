# GPT strategy review

Architecture-only review completed. The repository has fail-closed executable-edge, simulation, liquidity and sellability controls, but current evidence contains no fresh runtime forensics. Profitability, CANARY readiness and LIVE readiness therefore cannot be claimed. The principal accounting risk is that EVM BROADCAST rows may be counted using expected rather than realised net P&L. Solana SHADOW research currently lacks contemporaneous executable quote economics. Strategy/market losses and execution/infrastructure failures are not consistently classified in Strategy Lab observations.

## REWORK — Strategy Lab realised performance attribution
A broadcast is not confirmed execution, and expected edge is not realised money-weighted P&L. Only reconciled receipts and closed positions should contribute to profitability decisions. Pending, reverted, dropped and unreconciled transactions must remain execution outcomes rather than strategy profits.

## NEW_SHADOW — Solana current executable-edge adapter
Historical leader returns do not establish executable follower edge. Solana needs decision-time Jupiter route output, price impact, priority fee, base fee, refundable versus non-refundable rent, token-account costs, quote age, simulation result, sellability and follower latency measured at the intended notional.

## IMPROVE — Strategy versus execution loss attribution
STRATEGY/MARKET loss means a successfully executed and reconciled position lost after costs. EXECUTION/INFRASTRUCTURE failure includes stale quotes, simulation rejection, RPC failure, dropped or reverted transactions, reconciliation failure and failed-attempt fees. Combining or omitting them obscures whether selection or infrastructure needs correction.

## SHADOW_MORE — EVM atomic route cost model
The doubled-size impact metric is valuable as a liquidity/capacity gate, but subtracting it as though it were an additional intended-size cash cost may double-count impact already embedded in the exact quote. Conversely, failed-attempt gas and latency/adverse-selection reserves need empirical calibration rather than omission.
