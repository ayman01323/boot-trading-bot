# Gemini strategy review

Completed architecture-only strategy review due to absent runtime forensics evidence. Core SHADOW abstractions appropriately separate discovery from execution, but chain-specific accounting must more robustly distinguish infrastructure/RPC drops from genuine strategy loss, especially in high-volatility environments like Solana.

## IMPROVE — Solana and EVM Copy Trading
Static RPC thresholds on high-speed chains like Solana and varied latency EVM networks cause false negatives in SHADOW PnL. To accurately calculate durable money-weighted NET P&L after fees, slippage, and price impact, EXECUTION/INFRASTRUCTURE failures (e.g., RPC drops, priority fee estimation failures) must be strictly isolated from STRATEGY/MARKET loss.
