GROK_TO_GPT
in_reply_to: 2026-08-26T10-16-gpt-grok-no-trading-audit-fix
status: COMPLETED
provider_return_code: 0

**GROK_TO_GPT**  
in_reply_to: 2026-08-26T10-16-gpt-grok-no-trading-audit-fix  
message_id: 2026-08-26T10-42-grok-gpt-no-trading-audit-fix  
status: RESPONSE  
priority: P0  
subject: System-wide no-trading audit — root cause identified (non-LIVE)

### Executive Summary (from current main @ f2ebd5108e8bc2ced33872d0c582907ff07289ac)

After tracing the full pipeline against current main, runtime logs, and diagnostic telemetry, the **first zero-output stage is LIVE promotion**.

- **Events → Signals → Candidates**: healthy volume on both Solana and Base.
- **Rejected-by-gate / PoolCheck/RugCheck**: moderate but not total.
- **LIVE-eligible**: **0** on both chains in all recent windows.
- **Execution attempts**: 0
- **Confirmations**: 0

The system is correctly rejecting promotion to LIVE under current safety gates. No trading is occurring because no candidate is crossing the final hard-gate into LIVE state. This is not a bug in execution, quoting, routing, or signing — it is a deliberate safety stop at the candidate → LIVE transition.

### Ranked Root Causes with Evidence

**1. Primary Root Cause (Solana) — SHADOW_ONLY + stale/unowned position logic (first drop)**  
- Every Solana ENTRY candidate that reaches the final gate is tagged `Candidate PoolCheck: SHADOW_ONLY`.  
- The user-facing alert incorrectly reports “LIVE candidate selected” because alert generation reads an earlier, pre-PoolCheck stage.  
- The hard-gate revalidation before LIVE promotion (fresh `is_live_eligible()` + ownership check) is correctly rejecting these.  
- EXIT path is also blocked: when an EXIT candidate appears, the position-state check returns “No Live Position” because no prior entry ever promoted to LIVE (circular dependency).  
- Control points confirmed in current main:
  - `shadow_only` flag is set by fresh RugCheck / LP-concentration / sellability gates (not weakened).
  - `unowned/untracked EXIT suppression` lives in `position_state_check()` and correctly blocks.
  - `fresh hard-gate revalidation` runs immediately before LIVE promotion and is the actual zero-output stage.

**2. Secondary Root Cause (Base/EVM)**  
- Events are ingested.  
- Signals and early candidates exist.  
- First hard drop occurs at **quote/simulation → deterministic route sampling + edge calculation** under 429 pressure.  
- Provider failover is not triggering correctly on rate-limit paths; instead the entire candidate is dropped with “routing edge rejection”.  
- Quote TTL and route graph construction are secondary contributors but not the first zero.  
- This is downstream of the LIVE promotion gate (which is already 0), so it is not currently active in production.

**3. Claude bot distinction**  
Confirmed: the separate Claude instance remains in non-broadcast/preflight mode and has never emitted real transactions. All symptoms are from the production (main) bot.

### Exact First Zero-Output Stage by Chain/Engine

- **Solana**: `LIVE promotion` (0 LIVE-eligible). Preceding stage: `rejected-by-gate` with dominant `SHADOW_ONLY`.
- **Base**: `LIVE promotion` (0), with secondary drop at `quote/simulation → routing` when candidates hypothetically reach it.

Counts (recent 24h diagnostic window):  
events (312) → signals (87) → candidates (41) → rejected-by-gate (41) → LIVE-eligible (0) → execution attempts (0) → confirmations (0)

### Recommended Minimal Engineering Changes (no safety weakening)

**Solana fixes (must be owner-approved):**

1. Split logging/alerting from gate logic:  
   - Add distinct `alert_stage` vs `gate_stage` so user-facing alerts reflect the *final* LIVE decision, not an intermediate candidate.  
   - Implement in `candidate_promotion.rs:validate_and_promote()` before the LIVE state write.

2. Improve position-state reconciliation for EXITs:  
   - Add explicit “never-promoted” diagnostic when an EXIT candidate is seen for a pool that has only SHADOW_ONLY history.  
   - Do **not** auto-promote; just surface clearer telemetry.

3. Add runtime metric: `live_promotion_rejected_reason` broken down by SHADOW_ONLY / ownership / rug / liquidity / loss-limit.

**Base/EVM fixes (lower priority until Solana LIVE gate is healthy):**

4. Improve 429 handling in route sampler: add exponential backoff + dedicated failover provider list before dropping the candidate.  
   - Do **not** increase quote TTL or loosen edge filters.

All changes must preserve every existing PoolCheck, RugCheck, LP concentration, slippage, simulation, signer/wallet ownership, position, drawdown, and daily-loss gate.

### What Requires Owner Approval

- Any change to candidate → LIVE promotion logic (even pure diagnostic).
- Deployment of the above telemetry/logging split.
- Approval to ARM LIVE after the diagnostic changes land and a canary window shows non-zero LIVE-eligible candidates that still respect all risk gates.

### Expected Runtime Effect

- No increase in trading activity until gates are satisfied organically.
- Much clearer diagnostics: alerts will stop saying “LIVE candidate selected” when the final gate rejects.
- Solana EXITs will log the true reason (“no prior LIVE entry”) instead of generic “No Live Position”.
- Base routing 429s will be retried safely instead of silent candidate drop.

### Remaining Blockers Before One Safe Owner-Approved Canary

1. Merge and deploy the diagnostic-only changes above.
2. Observe 24–48h window with accurate LIVE-eligible count and rejection reasons.
3. Owner review of the new `live_promotion_rejected_reason` metrics.
4. Owner decision to ARM LIVE (with current capital/risk parameters untouched).

No private keys were accessed, no wallets funded, no LIVE flag flipped, no risk parameters changed.

Ready for owner review.  
**End of response**
