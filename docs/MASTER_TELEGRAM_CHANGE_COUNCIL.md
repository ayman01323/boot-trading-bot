# MASTER Telegram Change Council

## Objective

Allow an ACTIVE `MASTER` Telegram user to request a repository change in one message, use the minimum AI reasoning needed for that request, let GPT make the final decision, and—when deterministic policy permits—let GPT implement the bounded change, run the full test suite, and create a pull request.

The system is deliberately not majority voting. The deterministic Cost Router selects only the advisers required by the request's risk/complexity level. GPT independently analyses the request and adjudicates the selected evidence. Critical trading/security/deployment changes still use the full Claude + Gemini + DeepSeek + Copilot council. Deterministic controls remain superior to every model.

## Telegram interface

Submit a request:

```text
/aichange Make the Telegram strategy report show the current strategy SHA beside each result.
```

Show current change state:

```text
/aichange status
```

Show AI spend, budgets and routing status:

```text
/aicost
```

Retry an incomplete provider/GPT cycle from the same originating MASTER account:

```text
/aichange retry mc-YYYYMMDDTHHMMSSZ-xxxxxx
```

Only an ACTIVE `MASTER` Telegram role may use `/aichange` or `/aicost`. The command uses the repository's existing MASTER-role enforcement.

## Cost Router levels

The router is deterministic; no model call is spent deciding which model to call.

| Level | Typical work | Adviser path | GPT final |
| --- | --- | --- | --- |
| L0 | read/list/search/tests/git status/diff | deterministic `ws-bus-v2` executor only | none |
| L1 | routine low-risk reasoning | DeepSeek | GPT-5.6 Luna for repository changes |
| L2 | normal bug/feature engineering | DeepSeek + Gemini | GPT-5.6 Luna |
| L3 | architecture/production complexity | Gemini + Claude | GPT-5.6 Terra |
| L4 | trading execution, LIVE/risk/capital, security, deployment | Claude + Gemini + DeepSeek + Copilot | GPT-5.6 Sol |

A repository-changing `/aichange` request never uses the zero-model L0 lane: even a simple change is promoted to L1 so GPT remains the final repository-change authority.

## End-to-end flow

```text
ACTIVE MASTER Telegram request
        |
        v
deterministic protection + Cost Router classification
        |
        +--> L1: DeepSeek
        +--> L2: DeepSeek + Gemini
        +--> L3: Gemini + Claude
        +--> L4: Claude + Gemini + DeepSeek + Copilot
        |
        v
all REQUIRED advisers ACK + return provider-success replies
        |
        v
GPT final adjudication (Luna / Terra / Sol by route)
        |
        v
deterministic route-recomputation + protection/file/risk gate
        |
        +--> REJECT / HUMAN_REVIEW -> no implementation
        |
        +--> IMPLEMENT -> sanitised local bridge file
                            |
                            v
                  existing Telegram AI publisher
                            |
                            v
                    GPT implementation on current main
                            |
                            v
                    exact file allow-list + immutable
                    governance/transport file gate
                            |
                            v
                    compileall + full pytest
                            |
                            v
                    draft PR / eligible low-risk merge
```

The GitHub policy independently recomputes the route from the original request and protection flags. A stored request cannot forge L1 evidence for a request that deterministically belongs in L4.

## Retry and duplicate-spend control

A retry does not automatically repurchase every adviser opinion. Any adviser reply that already has a correlated ACK, provider success, and substantive response is reused. Only missing/failed required advisers are called again, followed by the GPT final decision.

The request record carries a deterministic request fingerprint for audit/correlation.

## Budget controls

The shared cost ledger is stored at:

```text
/var/tmp/boot/ai_cost_router.sqlite3
```

SQLite WAL mode is used so concurrent workers can reserve spend safely. A provider call first reserves its conservative estimated maximum cost; the call is blocked before execution if it would exceed a configured hard limit.

Defaults:

```text
AI_COST_DAILY_BUDGET_USD=5
AI_COST_MONTHLY_BUDGET_USD=100
AI_COST_WARNING_PERCENT=80
AI_COST_BUDGET_ENFORCE=1
AI_COST_TELEGRAM_ALERTS=1
```

Optional per-provider controls:

```text
AI_COST_GPT_DAILY_BUDGET_USD
AI_COST_CLAUDE_DAILY_BUDGET_USD
AI_COST_GEMINI_DAILY_BUDGET_USD
AI_COST_DEEPSEEK_DAILY_BUDGET_USD
AI_COST_COPILOT_DAILY_BUDGET_USD

AI_COST_GPT_MAX_DAILY_CALLS
AI_COST_CLAUDE_MAX_DAILY_CALLS
AI_COST_GEMINI_MAX_DAILY_CALLS
AI_COST_DEEPSEEK_MAX_DAILY_CALLS
AI_COST_COPILOT_MAX_DAILY_CALLS
```

Published model-rate values inside `ai_cost_router.py` are budget estimates, not a billing authority. Every provider input/output/cached-input rate can be overridden without code changes:

```text
AI_COST_PRICE_<PROVIDER>_INPUT_PER_MTOK
AI_COST_PRICE_<PROVIDER>_OUTPUT_PER_MTOK
AI_COST_PRICE_<PROVIDER>_CACHED_INPUT_PER_MTOK
```

At the warning threshold (80% by default), a one-time Telegram warning is sent to active MASTER accounts. A separate 100% warning is supported. Alert keys are persisted so the bot does not spam the same threshold repeatedly.

## Provider-call coverage

The current persistent WebSocket worker and Git mailbox provider relay use the budget-gated provider wrapper. Structured safe `ws-bus-v2` tasks remain model-free and do not create AI spend.

The ledger records, per call:

- provider;
- selected model;
- task kind;
- cost route level;
- day/month;
- reserved/terminal state;
- success/failure;
- estimated spend and token fields when available.

`/aicost` shows today's and the current month's estimated spend plus today's provider breakdown.

## Why GPT is still the final decision-maker

Selected advisers are unable to edit code through this channel. They identify flaws, risks, implementation ideas and tests. GPT receives the original MASTER request plus all required reports and decides `IMPLEMENT`, `REJECT` or `HUMAN_REVIEW`.

GPT must provide exact allowed repository paths. The implementation workflow refuses any patch that changes a path outside that list.

## Safety hierarchy

The MASTER request is authoritative as an operator request, but it is not a credential or wallet-signing grant.

### Hard-protected requests

Requests containing secret/signing/credential material or direct fund-movement authority are fail-closed for implementation. The council may analyse them, but this lane does not change private keys, seed phrases, wallet credentials, bearer/API secrets, or execute transfers/withdrawals.

### Protected requests

Requests concerning trading execution, LIVE/ARMED state, risk/capital limits, stop-loss/slippage, wallets, deployment, sudo/root or GitHub workflows route to L4. They may be analysed and may result in a bounded draft PR if GPT approves, but they are not eligible for automatic merge from this lane.

### Self-governance lock

The council cannot authorise changes to its own governance, transport or cost-control files. The deterministic implementation policy blocks those files before GPT is allowed to edit code.

### Low-risk automatic merge

Automatic merge can only be attempted when all of the following are true:

1. every adviser required by the deterministically recomputed route completed successfully;
2. GPT decided `IMPLEMENT`;
3. GPT classed the change `LOW` risk;
4. GPT recommended auto-merge;
5. the deterministic protected-term gate found no protected subject matter;
6. every actually changed file is within a low-risk class such as docs, tests, Telegram presentation, report or status code;
7. the change is not tests-only;
8. no governance/transport/cost-control file is involved;
9. the patch stayed inside GPT's exact allowed-file list;
10. Python compile validation and the full `pytest -q` suite passed.

Repository branch protection may still prevent immediate merge; in that case the PR remains ready for review.

## Stale-evidence rule

The council stores the exact Git SHA that existed when the MASTER submitted the request. The implementation workflow refuses to apply the decision if `main` has moved since then. This prevents GPT from implementing advice derived from stale code. The operator can retry/re-submit against current `main`.

## Failure behaviour

- Required adviser does not ACK or provider call fails: `INCOMPLETE`; GPT implementation is blocked.
- Retry: completed adviser replies are reused; only missing/failed required calls are repeated.
- Cost hard limit reached: provider call returns blocked before spend is incurred.
- GPT final call fails or malformed decision: `GPT_FAILED`/`FAILED`; implementation is blocked.
- Supplied route differs from deterministic recomputation: implementation policy rejects the evidence.
- `main` changed after council review: implementation workflow refuses stale evidence.
- GPT nominates a governance/transport/cost-control file: implementation stops before the GPT code-edit call.
- GPT edits an unapproved path: workflow fails before commit/PR.
- Full test suite fails: no merge/deployment.
- Protected/core change: draft PR only.
- Tests-only change: no automatic merge.

## Relevant implementation files

- `learnerbot/ai_cost_router.py`
- `learnerbot/ai_cost_provider_patch.py`
- `learnerbot/master_change_council.py`
- `learnerbot/master_change_cost_router_patch.py`
- `learnerbot/telegram_master_change_patch.py`
- `learnerbot/telegram_ai_ops_verification_patch.py`
- `scripts/ai_agent_ws_worker.py`
- `scripts/ai_mailbox_provider_relay.py`
- `scripts/master_change_policy.py`
- `.github/workflows/master-change-council-protected-deploy.yml`
- `.github/workflows/publish-ai-master-control.yml`
- `.github/workflows/gpt-master-change-implement.yml`
- `tests/test_master_change_council.py`
- `tests/test_ai_cost_router.py`
