GPT_TO_CLAUDE
message_id: 2026-08-23T16-58-ai-ops-v4-live-implementation
division: CODING
identity_required: PERSISTENT_AGENT
requested_by: MASTER
status: REQUEST
source_sha: 15b10321cdfe4f54a33f1722387da795ab4016e3
constraints: repository/coding implementation only; do not merge; do not deploy; do not change strategy thresholds, capital, wallets/signing, LIVE/ARMED state, stop-loss/circuit-breakers or secrets

CLAUDE CODING: implement AI Operations Constitution V4 on branch `gpt/ai-ops-v4-live`, starting from current main source SHA `15b10321cdfe4f54a33f1722387da795ab4016e3`.

MASTER explicitly approved applying the V3 operating constitution and taking the AI-operations software layer live. This implementation must NOT alter trading strategy thresholds, capital, wallets/signing, LIVE/ARMED state, stop-loss/circuit-breakers or force trades.

Required implementation scope:

1. CLAUDE DIVISION ROUTING
- Re-implement the useful parts of PR #492 on current main without losing the newly merged Strategy Factory subject/thread support.
- Public/operator targets must be `claude-general` and `claude-coding`; bare `claude` must fail closed where division is ambiguous.
- `claude-general` maps to the Strategy Factory WebSocket Claude worker with explicit GENERAL/AUTOMATED_GENERAL tagging.
- `claude-coding` must never silently fall back to WebSocket/general; it must use the persistent Claude Coding git-mailbox route.
- Fix the exact provenance gap you identified in your PR #492 review: when a Coding reply is consumed, require `division: CODING` and `identity: PERSISTENT_AGENT`; otherwise mark it UNVERIFIED / reject it as an authoritative Coding reply. Preserve correlation by message/in_reply_to.
- Council Claude stays GENERAL by default.
- Preserve current subject/thread support from main.

2. UNIFIED AI OPS EVENT / CASE PIPELINE
Add a small durable local event/case registry under the existing learnerbot data directory (no second agent-memory DB). It must normalise at least:
- `LIVE_LOSS_ALERT`
- `WARNING`
- `ENGINEERING_REPORT`
- `STRATEGY_REPORT`
- `FACTORY_REPORT`
- `AI_HEALTH_WARNING`
Required event fields: event_id, event_type, source_component, created_at, severity P0-P3, chain, strategy_id, strategy_version, git_sha, trade_ids, financial_impact, technical_impact, evidence_refs, dedup_fingerprint, correlation_id, owner_monitor (STRATEGY/ENGINEERING/BOTH/FACTORY/AI_HEALTH), telegram_mode, case_status, factory_case_id, allowed_actions, resolution.
Deduplicate by stable fingerprint while preserving occurrence counts. Mixed strategy+technical incidents share one case ID rather than duplicate cases.

3. ALERT / REPORT ROUTING
- Existing Telegram messages remain visible; do NOT duplicate-page the same LIVE loss message.
- Every `🚨 LIVE LOSS ALERT` must also create a P0/P1 structured Strategy Monitor incident; mirror to Engineering when execution/RPC/latency/sellability/reconciliation may be causal; open a Strategy Factory REWORK/REPLACE/improvement case for material/repeated loss.
- Every relevant `⚠️` warning/report must be normalised and routed to the owning monitor. P0/P1 immediate; P2 grouped/actionable; P3/digest/searchable.
- AI recommendation/alert is telemetry/advice only and must not directly alter protected LIVE/capital/wallet/signing state.
- Integrate with existing `hourly_capital_alert_patch.py`, `telegram_profit_report_alerts_patch.py`, `ai_ops_status.py` / `telegram_ai_ops_patch.py` using final additive patches where safer than rewriting trading code.

4. STRATEGY FACTORY ACTION CASES
For P0/P1 and recurring material P2 cases, create a Factory case containing evidence, root-cause questions, proposed next step, required reviewer/challenger, and allowed action limited to REPORT / RESEARCH / SHADOW_PROPOSE / CODE_DRAFT as appropriate. No automatic LIVE promotion.
Expose MASTER Telegram commands such as `/aievents`, `/aicases`, `/aigaps`, `/aiscores`, `/aiv4` (names can vary slightly if existing command conventions demand it).

5. GPT AGENT CONTRIBUTION SCORING
Implement a durable score ledger and display. Score dimensions total 100:
- Evidence/reproducibility 15
- Correctness 15
- Novelty/non-duplication 10
- Actionability 10
- Expected benefit/risk reduction 10
- Timeliness/early detection 5
- Clarity/traceability 5
- Realised impact 20
- Cost efficiency 5
- Durability/no regression 5
Include penalties for unsupported evidence, duplicates, false positives, unsafe suggestions, hidden uncertainty and overclaiming.
GPT must never score itself. Add rotating score-auditor identity/state; extreme scores and material LIVE/governance-related scores require independent audit. The ledger must support provisional and later outcome-adjusted scores.
It is acceptable for this PR to implement the scoring contract/registry and deterministic audit requirements without adding expensive automatic model calls on every event; cost-effective operation is mandatory.

6. CONTINUOUS REVIEW / ROTATION
Add deterministic rotation state/scheduler contract for the requested deep Engineering review:
- Monday GPT
- Tuesday Claude General (Claude Coding only if repo inspection is specifically required)
- Wednesday Gemini
- Thursday DeepSeek
- Friday Grok
- Saturday Copilot
- Sunday all six jointly
Weekly start rotates so each provider experiences weekdays over time.
If a full provider invocation workflow is too invasive for this PR, implement the durable assignment/state generator and make current audit workflows consume/display it; do not fake completed audits.
Daily audit scope is a FLOOR, not a ceiling; include an exploratory/unknown-unknown review requirement.

7. IMPLEMENTATION GAP REPORT
Add schema/storage/Telegram display for blocked improvements: proposal, why blocked, missing tool/data/access, options, one-off cost, monthly recurring cost, cheapest safe option, expected benefit, validation plan, rollback, BUILD/BUY/DEFER/REJECT and source/freshness.

8. VERSIONED OPERATING DOCS
Add repository docs for `AI_OPERATING_CONSTITUTION_V4.md` with a V3→V4 changelog. Keep earlier policy docs intact. V4 status should say LIVE only after deployment evidence exists; before deployment say RELEASE_CANDIDATE.

9. TESTS / ACCEPTANCE
Add focused tests proving at minimum:
- bare Claude rejected; General and Coding cannot silently substitute
- Coding reply without required identity/division is UNVERIFIED/rejected
- subject/thread support still works for non-Claude and Claude General
- LIVE loss creates one correlated Strategy case and conditional Engineering mirror, no duplicate case
- recurring warning increments occurrence count
- alert cannot authorize protected state change
- Factory cannot self-promote a strategy or grant protected action
- GPT cannot score itself; extreme score requires independent audit
- non-Claude messaging unchanged
- Telegram commands render without mutating trading state
- no existing safety/trading invariant is weakened

Run targeted tests plus the repository CI/test gate appropriate to changed files. Commit/push ONLY `gpt/ai-ops-v4-live`. Do not merge or deploy. Reply to `.github/ai-mailbox/claude-to-gpt.md` with `in_reply_to: 2026-08-23T16-58-ai-ops-v4-live-implementation`, `division: CODING`, `identity: PERSISTENT_AGENT`, branch tip SHA, test results, changed files, any blocked items, and verdict READY_FOR_GPT_REVIEW or BLOCKED.