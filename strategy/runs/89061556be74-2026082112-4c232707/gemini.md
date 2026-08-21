# Gemini strategy review

A complete, architecture-only strategy review of the trading bot repository and strategy cycle evidence. Because .strategy_cycle/evidence.json reports MISSING_RUNTIME_FORENSICS, we strictly enforce the prohibition of any live or canary profitability claims. The core architecture of the Strategy Laboratory (strategy_lab.py) and the copy-trading engines (sibot.py and solana_sibot.py) is robust, providing clean sandboxing of shadow research. We propose shadow-testing existing built-in families (Cross Venue Net Arbitrage, Learned Route Replication, and SiBot Leader Copy) to gather out-of-sample metrics, alongside proposing a new uncorrelated spread-imbalance shadow strategy, ensuring that all live safety and capital gates remain fully intact.

## NEW_SHADOW — Dynamic Spread Execution
By relying on direct on-chain liquidity indicators and execution spread divergence instead of individual wallets or raw price momentum, the bot can discover uncorrelated micro-arbitrage or slippage reversion opportunities that bypass copy-trading latency limits.

## SHADOW_MORE — Cross Venue Net Arbitrage
Verify executable cross-venue discrepancies under live conditions before any live execution adapters are implemented. Given that we have no fresh runtime evidence, this strategy must continue shadow testing to establish a baseline for actual transaction-flow latency and net profit after fees.

## SHADOW_MORE — Learned Route Replication
Verify if historical profitable routes (stored in strategy_patterns database) remain replicable when replayed without following a specific leader wallet. Continuing shadow testing will verify whether public route re-evaluations remain profitable under changing market conditions.

## SHADOW_MORE — SiBot Leader Copy
Maintain leader-copying shadow metrics under Strategy Lab registry across all enabled EVM and Solana chains to verify net profit after gas/fees. Given that fresh runtime evidence is unavailable, we must gather more shadow performance and exit data to confirm leader reliability.
