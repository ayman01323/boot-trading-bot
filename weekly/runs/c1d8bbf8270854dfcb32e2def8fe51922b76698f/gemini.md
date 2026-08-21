# Gemini weekly audit

Identified a critical execution failure in the Solana LIVE exit circuit breaker where newly introduced atomic sell error messages are ignored by the reconciliation loop due to a hardcoded string mismatch. This strands capital and permanently halts trading for the affected user when RPC lag occurs.

## P1 — Solana LIVE atomic sell errors permanently block circuit-breaker reconciliation due to string mismatch
When a Solana LIVE atomic full sell experiences RPC lag or lands with a zero-balance decrease, the position is placed into the exit circuit with `LANDED_INVALID` status. Because the error messages do not match the hardcoded SQL `LIKE` clause, the reconciliation loop permanently ignores the position. This freezes the user's LIVE trading state indefinitely and abandons the position without attempting an automatic retry.
Corrective action: Modify the `reconcile_pending_exit_circuits` SQL query to include the new atomic sell error strings, or remove the strict string filter from the `LANDED_INVALID` clause and handle the exact failure classification within Python.
