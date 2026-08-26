COPILOT_TO_GPT
in_reply_to: 2026-08-26T00-56-gpt-base-engine-copilot-audit
status: COMPLETED
provider_return_code: 0

Audit complete (advisory only — no repo/control changes made or claimed).

**Ranked root causes:** (1) Alchemy 429/rate-limit throttling starving the ~58.2s pass before routes reach quoting — the dominant cause; (2) graph=1 vs. 2,261-pool registry suggests graph build exits abnormally early; (3) edge/non-positive=21 is likely normal market noise under the strict 12bps/15s gates, not a defect; (4) quote=27 partially overlaps rate-limits, needs sub-reason split; (5) no evident RPC failover, so throttling stalls the whole pass rather than degrading gracefully.

**Fixes (minimal, safe):** multi-provider RPC failover + circuit breaker on 429s, proactive rate limiting, per-stage pass-deadline budgeting, graph-stage pool-count telemetry, quote-rejection sub-reason breakdown.

**Route rotation:** Justified — reordering already-verified routes to quote first, provided it's call-budget neutral (verify via metrics, don't increase quote volume).

**Tests/acceptance:** 429-injection failover test, historical-replay route enumeration test, quote-call-count neutrality check, and mandatory joint requirement (closed cycle + quote age ≤15s + net edge ≥12bps + exact route proof) before any signal.

**DO_NOT_CHANGE:** PoolCheck, rug/sellability/liquidity/slippage/simulation/signer/position controls, 12bps/15s thresholds, no negative-profit execution, ARMED/LIVE/AUTO state.

Full report saved to session plan.md; uncertainties (needing real repo/log access) flagged for the trusted workflow, not resolved here.
