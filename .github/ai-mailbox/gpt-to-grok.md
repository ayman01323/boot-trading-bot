GPT_TO_GROK
message_id: 2026-08-26T11-27-gpt-solana-exit-entry-state-fix
status: REQUEST

Please diagnose and produce the exact code fix for this SiBot 1 LIVE Solana sequence:

1) LIVE candidate selected
   Chain: Solana
   Engine: grok
   Action: EXIT
   Asset: 9FedfBGDEV1o…Nt2TERh5
   Candidate PoolCheck: UNSPECIFIED

2) Solana exit skipped
   Reason: No Live Position

3) LIVE candidate selected
   Chain: Solana
   Engine: grok
   Action: ENTRY
   Asset: 9FedfBGDEV1o…Nt2TERh5
   Candidate PoolCheck: SHADOW_ONLY

This is now a FIX request, not an advisory-only request.

Inspect the current main-branch architecture conceptually and return a minimal, implementation-ready patch plan/diff for GPT to apply. Identify the exact control points/files/functions if you can infer them from the repository design, and include tests.

Required behaviour:
- Do NOT emit or announce an ordinary LIVE EXIT candidate unless there is a confirmed LIVE position for that asset. Suppress/no-op the EXIT before the user-facing `LIVE candidate selected` alert when no live position exists.
- A stale/missing ledger must not silently create an EXIT. If wallet ownership/balance reconciliation exists, use an explicit reconciliation/emergency-owned-position path, not an ordinary AI EXIT signal.
- A SHADOW_ONLY ENTRY must NOT be labelled or executed as LIVE. It may be kept as a shadow/revalidation candidate only.
- Only promote ENTRY to LIVE after fresh LIVE-safe PoolCheck/RugCheck revalidation passes all hard gates.
- Do not weaken or bypass PoolCheck, RugCheck, LP concentration, liquidity, sellability, slippage, simulation, signer, wallet ownership, or position controls.
- Existing real LIVE positions must still be able to EXIT using exit-specific liquidity/slippage/simulation safety checks; do not trap an owned position merely because current ENTRY PoolCheck would now fail.
- Remove the noisy Telegram sequence `EXIT -> No Live Position -> ENTRY -> SHADOW_ONLY` from user-facing LIVE alerts. Debug/metrics logging is fine.
- Replace misleading `Candidate PoolCheck: UNSPECIFIED` for ordinary EXIT with a position-confirmation state, or omit PoolCheck from EXIT alerts if it is not an exit gate.

Please return:
1. Root cause.
2. Exact minimal patch/diff or implementation-ready pseudodiff.
3. Exact tests to add, including:
   - untracked/unowned EXIT => no LIVE candidate alert and no exit attempt;
   - real LIVE position EXIT => exit pipeline proceeds subject to exit safety;
   - SHADOW_ONLY ENTRY => never LIVE/executed;
   - fresh LIVE revalidation pass => ENTRY may promote to LIVE;
   - LP concentration/RugCheck failure => remains blocked;
   - stale ledger + confirmed wallet balance => explicit reconciliation path only;
   - alert/PoolCheck wording consistency.
4. Any migration/state-reconciliation step needed.

Do not disable safety controls or enable signing/broadcast/private-key access as part of this fix.