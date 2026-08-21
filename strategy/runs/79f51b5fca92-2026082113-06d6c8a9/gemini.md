# Gemini strategy review

Completed architecture-only strategy audit. The existing modular foundations (Strategy Lab registry, fail-closed feature adaptation, and SQLite-based tracking) are highly cohesive and solid. However, no fresh runtime evidence is available in .strategy_cycle/evidence.json to verify strategy profitability or support canary/live promotion. The major architectural gap is the lack of a closed-loop outcome layer that maps actual filled trade receipts (such as live_execution_receipts) and execution failures back to Strategy Lab scorecards, and a contemporary quoting adapter for Solana leader events. No strategy is verified as live-ready.

## IMPROVE — Multi-Chain Closed-Loop Realised P&L Attribution
Durable strategy ranking requires money-weighted net P&L based on verified wallet cash flows rather than idealised simulation quotes. Incorporating actual transaction execution receipts (net of fees, slippage, and price impact) into the strategy_lab SQLite databases allows the system to compute true out-of-sample returns and cleanly separate strategy/market losses from execution/infrastructure failures.

## IMPROVE — Solana Contemporary Quoting Feature Adapter
Without contemporaneous pricing and liquidity quotes, Solana leader events cannot be simulated as executable opportunities in Strategy Lab. Fetching real-time Jupiter quotes at decision-time and populating the market features schema resolves the missing edge gap.

## IMPROVE — EVM Execution Failure Cost Modeler
EVM strategies (especially cross-venue arb or learned-route replication) suffer from transaction reverts due to competitive gas pricing or block latency. Charging wasted gas costs from failed, reverted, or replaced transactions back to the initiating strategy prevents unprofitable models from looking profitable on paper.

## IMPROVE — Regime-Aware Promotion Governance
Promotion from canary stages must be governed by statistically significant samples across distinct market/fee regimes to protect capital from overfitting, selection bias, or lucky market streaks.
