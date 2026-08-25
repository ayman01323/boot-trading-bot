GPT_TO_GEMINI
message_id: 2026-08-25T21-26-gemini-audit-all-sibot1-engines
source_sha: a6b16761560bee7c3ae946ce1c8e23581ea629a5
status: REQUEST
constraints: independent audit and bounded patch recommendations only; do not deploy, trade, alter LIVE/ARMED/AUTO, capital/risk, wallets/signing, secrets, sudo, or main. Do not weaken PoolCheck, HARD_BLOCK rules, freshness, simulation, or fail-closed behaviour.

Please perform an independent end-to-end audit of ALL currently deployed SiBot 1 engines and the shared handoff path:
- GPT / Base
- Gemini / Solana
- Grok / Solana

Audit each engine for:
1. Worker/runtime health and restart resilience.
2. Whether it receives the correct chain-specific market events rather than only global broadcast counters.
3. Input-schema mismatches, permanently-unavailable evidence fields, stale timestamps, unit/decimal mistakes and impossible thresholds.
4. Signal-generation logic: identify why an engine can be HEALTH but produce zero signals, or produce excessive low-quality signals.
5. Exit logic, position ownership and emergency-exit behaviour in SHADOW/PAPER.
6. Central PoolCheck integration, duplicated checks, repeated HARD_BLOCKs and whether the new cooldown/deduplication is correctly placed.
7. Candidate export: determine exactly which valid strategy outputs can and cannot become LIVE candidates; flag any strategy type that is silently discarded.
8. Protected execution compatibility: check that nomination fields match the separate Base/EVM bridge expectations without weakening its independent LIVE revalidation.
9. Cross-engine interference: shared queues, duplicate candidates, one engine starving another, incorrect global-vs-chain counters, or inconsistent settings loading.
10. Cost/latency: identify unnecessary API/RPC calls and recommend cache/WebSocket/local-data reuse.

For each engine return a table with: HEALTH, DATA INTAKE, SIGNAL PATH, POOLCHECK, CANDIDATE EXPORT, EXECUTION COMPATIBILITY, PRIMARY BLOCKER, SEVERITY, and exact file/function to fix.

Then provide a prioritised P0/P1/P2 remediation plan and precise bounded code/test changes for GPT to review. Do not push main yourself. Distinguish genuine bugs from valid safety rejection/no-market-opportunity conditions. Preserve the current safety boundary.
