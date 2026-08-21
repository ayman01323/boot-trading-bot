# Gemini strategy review

Successful architecture review completed. Due to MISSING_RUNTIME_FORENSICS, all strategies must remain in SHADOW evaluation. The structural invariants in strategy_lab.py, sibot.py and solana_sibot.py correctly enforce execution economics (gas limits, priority fees, and roundtrip loss constraints), but fresh execution evidence is required to quantify real slippage, infrastructure failure rates versus strategy losses, and executable PnL before any promotion can occur.

## SHADOW_MORE — solana_sibot_copy_leader
Without fresh runtime records to validate Solana network conditions, we cannot confirm if 0.00002 SOL is sufficient for priority inclusion or if 3% loss guard prevents toxic MEV sandwiching. Continued SHADOW tracking is necessary to isolate executable alpha from block-inclusion/infrastructure failures.

## SHADOW_MORE — evm_sibot_top20_copy
EVM copy trading requires precise differentiation between strategy loss (bad entries) and infrastructure failure (reverts, out-of-gas, missed signals). We must validate the 250k gas cap against current mempool spikes before asserting positive executable edge.
