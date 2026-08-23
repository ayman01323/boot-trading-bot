# Gemini strategy review

Architecture-only review completed. The repository correctly prioritizes money-weighted net results and enforces strict risk controls, but holds critical structural gaps. Solana observations remain structurally fail-closed and ineligible due to lack of a real-time quote/simulation adapter, despite solana_sibot possessing a functional Jupiter client. EVM live recording conflates expected preflight returns with realized net P&L on broadcast or fee-pending rows, masking slippage and execution overhead. Strategy Lab's promotion engine lacks chronological out-of-sample holdout validation to protect against overfitting. No live readiness or profitability is supported without fresh runtime evidence.

## IMPROVE — Solana market feature adaptation
Solana strategies are structurally fail-closed and unable to satisfy Strategy Lab's common eligibility filters. Reusing the existing jupiter_quote engine from solana_sibot to populate Solana features with real-time, quote-backed slippage and fee estimates resolves this gap.

## IMPROVE — EVM direct-market arbitrage
Substituting expected preflight edge for realized edge on fee-pending or broadcast rows obscures execution failures, reverts, replacement gas costs, and actual slippage. This leads to artificial profitability figures in Strategy Lab.

## REWORK — Strategy Lab promotion evaluation
Evaluating a strategy on aggregate in-sample data is highly susceptible to overfitting to specific market regimes. Splitting the evaluation into chronologically disjoint out-of-sample windows ensures durability and prevents a single lucky window or outlier trade from causing premature promotion.
