# Gemini strategy review

Architecture-only review completed due to missing runtime forensics. The codebase robustly separates SHADOW from LIVE evaluation via `strategy_lab.py`. Proposals focus on deepening shadow tests for Flow Acceleration to model slippage under volume, and introducing a new shadow hypothesis targeting EVM gas dynamics.

## SHADOW_MORE — Flow Acceleration
While structurally sound, the Flow Acceleration strategy's required 4bps net edge might be consumed entirely by the price impact the acceleration causes. Further shadow testing with penalized latency and slippage estimates is necessary before live probation.

## NEW_SHADOW — Gas-Adjusted Early Divergence
Create a new research strategy that specifically isolates EVM price dislocations where gross edge temporarily outpaces local base fee spikes, requiring latency_reserve_bps to confirm priority fee buffering.
