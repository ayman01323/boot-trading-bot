# Gemini strategy review

Architecture-only strategy review completed. The repository correctly models transaction verification, gas-sensitive profit multiples, and fee-adjusted net P&L. However, due to MISSING_RUNTIME_FORENSICS in evidence.json, no strategies can be claimed as profitable, CANARY-ready, or LIVE-ready. Crucially, the Solana market feature adapter deliberately defaults to zero edge and zero cost features (SIGNAL_ONLY_MISSING_CURRENT_EXECUTABLE_EDGE), making Solana signal-edge testing impossible without contemporaneous quote tracking. The Strategy Lab promotion logic also needs rework to implement strict chronological out-of-sample holdout testing and separate signal-level losses from execution-stage failures.

## IMPROVE — Solana leader-copy and Strategy Lab families
Historical leader events do not contain follower-executable contemporaneous prices or market depth. Evaluating signal viability on Solana requires capturing real-time follower-sized entry and reverse-exit quotes immediately following a leader block confirmation.

## REWORK — Strategy Lab lifecycle promotion governance
Small sample sizes are vulnerable to overfitting, where a single large winner or favorable regime masks a structurally unprofitable signal. Enforcing a mandatory chronological train/validation/holdout partition with frozen parameters is necessary for safe promotion.

## IMPROVE — Strategy Lab decision and replacement engine
A strategy signal that is structurally profitable but suffers from transient RPC errors, transaction revert on-chain, or quote expiration should be optimized via infrastructure adjustments. Conversely, a signal that successfully executes but repeatedly loses money represents a core strategy defect.

## KEEP — EVM atomic cycle execution
The existing EVM execution path correctly ensures that expected returns are positive after accounting for actual simulated gas costs, priority/builder fees, and slippage. Maintaining this fail-closed posture is essential for capital preservation.
