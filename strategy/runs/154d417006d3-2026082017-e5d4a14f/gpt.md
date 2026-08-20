# GPT strategy review

Architecture-only review completed. The repository has useful fail-closed quote, simulation, liquidity, sellability and cost gates, but no fresh runtime forensics support profitability, CANARY or LIVE readiness. Solana Strategy Lab correctly records missing executable-edge evidence, while EVM shadow accounting includes gas, builder fees, slippage, impact and latency reserves. The principal architectural risk is promotion accounting: canary state is keyed only by strategy and aggregates native-base P&L across potentially heterogeneous EVM chains, which is not valid money-weighted P&L. Strategy/market losses and execution/infrastructure failures are partly separated, but require a richer common attribution ledger.

## KEEP — Cross-chain Strategy Lab executable-edge gating
These controls align selection with executable net edge instead of win count and prevent absent measurements from becoming trades.

## REWORK — Strategy canary outcome scoring
Adding unlike native assets produces invalid aggregate P&L and can let a small sample or changing trade size distort promotion decisions.

## NEW_SHADOW — Solana leader-following and signal strategies
Historical leader returns cannot establish the follower's executable edge after copy latency, Jupiter route impact, priority fees, slippage, rent cash lock-up and sellability.

## IMPROVE — Cross-chain outcome attribution
STRATEGY/MARKET loss means a successfully executed position lost after all costs. EXECUTION/INFRASTRUCTURE failure means quote, simulation, RPC, signing, broadcast, landing, confirmation or accounting failed; it may still incur fees or adverse inventory. These categories need separate rates and economic totals.
