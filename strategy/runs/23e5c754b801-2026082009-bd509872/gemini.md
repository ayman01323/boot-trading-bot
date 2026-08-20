# Gemini strategy review

Architecture-only review completed as runtime forensics were unavailable. Identified two critical execution and ranking flaws that risk durable money-weighted NET P&L. Solana's shadow execution relies on a hardcoded, highly optimistic exit fee that ignores priority fees, leading to falsely profitable shadow outcomes. EVM's leader history reconstruction drops all swaps not strictly paired with the native token, risking severe survivorship bias if leaders route losses through stables.

## IMPROVE — Solana SiBot Copy Strategy
A hardcoded 0.00002 SOL fee vastly underestimates priority fees and Jito tips required for guaranteed execution during network congestion. This artificially inflates SHADOW profitability and risks promoting unprofitable strategies to LIVE execution.

## REWORK — EVM SiMo Leader Selection
Filtering only direct native/token pairings silently drops trades routed through intermediate ERC20s (e.g., USDC, USDT). If a leader regularly exits losing positions to stables, these losses are ignored, creating severe survivorship bias and falsely inflating the leader's win rate and net profit.
