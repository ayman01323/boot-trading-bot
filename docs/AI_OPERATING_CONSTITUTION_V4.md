# AI Operations Constitution V4

Status: **RELEASE_CANDIDATE** until protected deployment evidence confirms this exact V4 merge SHA is running in production.

Date: 23 August 2026

V4 implements the approved V3 operating constitution as an AI-operations, monitoring and governance layer. It does not itself change strategy thresholds, capital, LIVE/ARMED state, wallets/signing, stop-losses, circuit-breakers or force a trade.

## 1. Version history

- **V1** — original four-layer operations plan: AI health, Engineering Monitor, Strategy Monitor, Strategy Factory/implementation.
- **V2** — adaptive-control design: six-agent model, rotating Engineering audit concept, bandwidth attribution, Monitor Evolution, Research Radar, stronger canary interpretation and shared state ownership.
- **V3** — all-agent operating constitution: evidence/challenge/synthesis/implementation lifecycle, severity-gated alert routing, GPT contribution scoring with independent audit, continuous review, implementation-gap reporting and marketing governance.
- **V4** — software implementation release: durable event/case/score/gap registries, live alert/report capture, Strategy Factory incident triage, explicit Claude General/Coding routing and Telegram V4 operations views.

Earlier version documents remain historical records; V4 does not overwrite them.

## 2. Decision constitution

Material work follows:

`INITIATE -> CLASSIFY -> INDEPENDENT CHALLENGE -> RESEARCH -> GPT SYNTHESIS -> AUTHORITY CHECK -> IMPLEMENT -> TEST -> OBSERVE -> SCORE & LEARN`

GPT is the evidence synthesiser/adjudication router, not a majority-vote counter. Minority objections remain evidence. An AI consensus does not override deterministic safety or MASTER approval requirements.

Decision classes remain cost-routed:

- **L0** deterministic/mechanical: zero model calls when possible.
- **L1** routine: one suitable agent.
- **L2** material: originator + independent challenger.
- **L3** important architecture/monitor/strategy work: several diverse reviewers.
- **L4** critical/protected: full advisory Council where practical plus existing deterministic/MASTER gates.

## 3. Claude divisions

Claude is now an explicit two-division destination:

- `claude-general` — Strategy Factory WebSocket adviser for governance, architecture, research synthesis and challenge. Messages are tagged `CLAUDE_DIVISION: GENERAL` / `AUTOMATED_GENERAL`.
- `claude-coding` — persistent repository/coding identity using the dedicated Git mailbox. There is no WebSocket fallback to General.

Bare `claude` is rejected where a recipient division is ambiguous.

A Coding reply is not authoritative merely because a file says it is. V4 requires correlation plus `division: CODING` and `identity: PERSISTENT_AGENT` before a consumer accepts it as the Coding reply. This is a fail-closed header/provenance floor, not cryptographic session attestation; stronger signed/session-auth provenance remains a future hardening option.

Council use of Claude is General by default. Coding is an implementation identity, not an advisory-vote alias.

Subject/thread support from V3-era Strategy Factory messaging is preserved.

## 4. Unified event and case registry

V4 records structured evidence under the existing learnerbot data directory in `ai_ops_v4/`; it does not create a second agent-memory database.

Supported event classes include:

- `LIVE_LOSS_ALERT`
- `WARNING`
- `ENGINEERING_REPORT`
- `STRATEGY_REPORT`
- `FACTORY_REPORT`
- `AI_HEALTH_WARNING`

Each event can record:

- event/correlation identifiers
- event type/source/time/severity
- chain, strategy ID/version and Git SHA
- trade IDs
- financial and technical impact
- evidence references
- stable dedup fingerprint and occurrence count
- owner monitor: Strategy / Engineering / Both / Factory / AI Health
- Telegram mode: Immediate / Grouped / Digest
- case and Factory identifiers
- allowed actions and resolution

Protected action names are explicitly denied by the event/case layer. An AI warning cannot grant authority to alter LIVE/ARMED, capital, wallet/signing, deployment, stop-loss, circuit-breaker or equivalent protected state.

## 5. Telegram and monitor routing

Existing Telegram alerts remain the operator-facing message; V4 records/routs the evidence without deliberately duplicating the same LIVE-loss page.

Severity policy:

- **P0/P1** — immediate operator attention and structured case.
- **P2** — grouped/actionable; a recurring P2 becomes a Factory/monitor case after the configured recurrence threshold.
- **P3** — digest/searchable evidence.

Routing:

- strategy/economic/position/entry/exit/liquidity evidence -> Strategy Monitor
- RPC/latency/execution/server/API/bandwidth/database evidence -> Engineering Monitor
- mixed evidence -> BOTH, sharing the same correlation/case ID
- AI health warning -> AI Health lane
- material/repeated cases -> Strategy Factory evidence case

A LIVE loss becomes a Strategy case. If the loss alert includes execution/sellability/RPC/latency/reconciliation evidence or an exit is pending, the case is shared with Engineering.

## 6. Strategy Factory incident triage

Serious and recurring material cases are queued to a bounded Strategy Factory GPT triage worker. The prompt is report/research-only and explicitly forbids protected state changes.

Triage must distinguish:

- directly proven facts
- competing hypotheses
- Engineering checks
- Strategy checks
- one adversarial falsification test
- next allowed Factory action
- missing data/tools/cost when blocked

Allowed Factory actions are bounded to:

- `REPORT`
- `RESEARCH`
- `SHADOW_PROPOSE`
- `CODE_DRAFT` where an Engineering case justifies a bounded code draft

The Factory cannot self-promote a strategy or grant itself protected authority.

## 7. GPT contribution scoring

The V4 ledger scores contribution quality up to 100 points:

- Evidence/reproducibility — 15
- Correctness — 15
- Novelty/non-duplication — 10
- Actionability — 10
- Expected benefit/risk reduction — 10
- Timeliness/early detection — 5
- Clarity/traceability — 5
- Realised impact — 20
- Cost efficiency — 5
- Durability/no regression — 5

Penalties cover unsupported evidence, duplication, false positives, unsafe suggestions, hidden uncertainty and overclaiming.

Rules:

- no agent may score its own contribution
- GPT therefore cannot score GPT
- extreme scores and material LIVE/governance-related scores require independent audit
- score auditor rotates
- scores start provisional and carry 7-day/30-day outcome-review dates
- score reputation never overrides deterministic safety, quorum or MASTER approval

## 8. Engineering review rotation

V4 stores a deterministic rotating assignment contract:

- Monday — GPT
- Tuesday — Claude General (Claude Coding only when repository inspection is specifically assigned)
- Wednesday — Gemini
- Thursday — DeepSeek
- Friday — Grok
- Saturday — Copilot
- Sunday — all six jointly

The weekly start shifts so providers rotate through weekdays over time. Every deep review requires an exploratory/unknown-unknown component. The originating author cannot be the sole reviewer of its own material change.

This assignment record must never be presented as a completed audit unless the corresponding review evidence actually exists.

## 9. Implementation Gap Report

When an improvement is useful but cannot safely be implemented, V4 stores a gap report rather than dropping the idea. Required decision outcomes are `BUILD`, `BUY`, `DEFER` or `REJECT` and the report must include at least:

- proposal
- why blocked
- missing tool/data/access
- one-off and recurring cost where known
- cheapest safe option
- expected benefit
- validation plan
- rollback
- source/freshness where external pricing or capability evidence is used

## 10. MASTER Telegram commands

V4 adds read-only MASTER views:

- `/aievents` — recent structured events
- `/aicases` — monitor/Factory cases
- `/aiscores` — contribution score ledger and current auditor
- `/aigaps` — implementation-gap reports
- `/aiv4` — V4 status, review assignment and record counts
- `/aicoding MESSAGE_ID` — fail-closed verification of a returned Claude Coding reply

`/aichat` requires `claude-general` or `claude-coding`; bare Claude is rejected.

## 11. V3 -> V4 implementation changelog

Implemented from V3:

1. V3 decision/authority boundaries encoded into V4 event/case protected-action denials.
2. Severity/dedup/ownership schema for warnings, reports and LIVE-loss evidence.
3. Existing LIVE-loss Telegram alerts feed Strategy cases and conditional Engineering evidence without intentional duplicate paging.
4. Material/repeated cases feed bounded Strategy Factory triage.
5. GPT contribution-score contract with independent audit requirement.
6. Implementation-gap/cost ledger.
7. Engineering rotation and unknown-unknown review requirement.
8. Claude General/Coding route separation while preserving Strategy Factory subject threads.
9. Coding reply identity/correlation verification wired as a fail-closed consumer path.
10. MASTER Telegram V4 status views.

Still not represented as autonomous authority:

- no automatic strategy promotion
- no automatic capital changes
- no wallet/signing action
- no automatic protected deployment from an alert/score/case
- no guaranteed-profit or win-rate claim

## 12. Release acceptance

Before changing status from `RELEASE_CANDIDATE` to `LIVE`, require evidence that:

- focused V4 tests pass
- existing relevant messaging/Telegram/trading-invariant tests pass
- PR merges cleanly against current main
- the normal protected deployment gate succeeds for the exact merge SHA
- learnerbot restarts healthy
- the final runtime integrity gate still passes
- `/aiv4` is available after deployment
- no strategy/capital/wallet/signing configuration was modified as part of the V4 release

After those checks, update this document to `Status: LIVE` and record merge/deployment evidence.
