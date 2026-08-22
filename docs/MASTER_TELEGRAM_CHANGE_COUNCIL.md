# MASTER Telegram Change Council

## Objective

Allow an ACTIVE `MASTER` Telegram user to request a repository change in one message, obtain independent advice from every non-GPT agent over the local zero-token WebSocket transport, let GPT make the final decision, and—when deterministic policy permits—let GPT implement the bounded change, run the full test suite, and create a pull request.

The system is deliberately not majority voting. Claude, Gemini, DeepSeek and Copilot are independent advisers. GPT independently analyses the request and then adjudicates their evidence. Deterministic controls remain superior to GPT.

## Telegram interface

Submit a request:

```text
/aichange Make the Telegram strategy report show the current strategy SHA beside each result.
```

Show current state:

```text
/aichange status
```

Retry an incomplete provider/GPT cycle from the same originating MASTER account:

```text
/aichange retry mc-YYYYMMDDTHHMMSSZ-xxxxxx
```

Only an ACTIVE `MASTER` Telegram role may use `/aichange`. The command never trusts a hard-coded Telegram number; it uses the repository's existing MASTER-role enforcement.

## End-to-end flow

```text
ACTIVE MASTER Telegram request
        |
        v
local request record + request_id + exact current Git SHA
        |
        v
local WebSocket bus (routing uses zero model calls)
        |
        +--> Claude adviser
        +--> Gemini adviser
        +--> DeepSeek adviser
        +--> Copilot adviser
        |
        v
all four must ACK + return a substantive provider-success reply
        |
        v
GPT final adjudication
        |
        v
deterministic protection/file/risk gate
        |
        +--> REJECT / HUMAN_REVIEW -> no implementation
        |
        +--> IMPLEMENT -> sanitised local bridge
                            |
                            v
                    trusted GitHub Actions bridge
                            |
                            v
                    GPT implementation on current main
                            |
                            v
                    exact file allow-list check
                            |
                            v
                    compileall + full pytest
                            |
                            v
                    draft PR
                            |
                            +--> LOW-risk safe presentation/reporting/docs/tests only:
                            |       deterministic auto-merge may be attempted
                            |
                            +--> trading/LIVE/risk/deployment/core changes:
                                    draft PR remains for review
```

## Why GPT is the final decision-maker

The advisers are intentionally unable to edit code through this channel. They identify flaws, risks, implementation ideas and tests. GPT receives the original MASTER request plus all four independent reports and must decide `IMPLEMENT`, `REJECT` or `HUMAN_REVIEW`.

GPT must provide exact allowed repository paths. The implementation workflow refuses any patch that changes a path outside that list.

This avoids five agents racing to edit the same branch and makes the final accountability chain clear:

```text
MASTER instruction -> multi-agent evidence -> GPT decision -> deterministic gate -> GPT patch
```

## Safety hierarchy

The MASTER request is authoritative as an operator request, but it is not a credential or wallet-signing grant.

### Hard-protected requests

Requests containing secret/signing/credential material or direct fund-movement authority are fail-closed for implementation. The council may analyse them, but this lane does not change private keys, seed phrases, wallet credentials, bearer/API secrets, or execute transfers/withdrawals.

### Protected requests

Requests concerning trading execution, LIVE/ARMED state, risk/capital limits, stop-loss/slippage, wallets, deployment, sudo/root or GitHub workflows may be analysed and may result in a bounded draft PR if GPT approves. They are not eligible for automatic merge from this lane.

### Low-risk automatic merge

Automatic merge can only be attempted when all of the following are true:

1. all four advisers completed successfully;
2. GPT decided `IMPLEMENT`;
3. GPT classed the change `LOW` risk;
4. GPT recommended auto-merge;
5. the local deterministic protected-term gate found no protected subject matter;
6. every actually changed file is within a low-risk class such as docs, tests, Telegram presentation, report or status code;
7. the patch stayed inside GPT's exact allowed-file list;
8. Python compile validation and the full `pytest -q` suite passed.

Repository branch protection may still prevent immediate merge; in that case the PR remains ready for review.

## Stale-evidence rule

The council stores the exact Git SHA that existed when the MASTER submitted the request. The implementation workflow refuses to apply the decision if `main` has moved since then. This prevents GPT from implementing advice derived from stale code. The operator can retry/re-submit against current `main`.

## Cost strategy

Routine coordination stays cheap:

- WebSocket routing, delivery, ACK and SQLite durability: zero model calls;
- Claude/Gemini/DeepSeek/Copilot advisers: each uses its configured low-cost routine worker model;
- GPT final adjudication: one stronger model call (`AI_MASTER_CHANGE_GPT_MODEL`, defaulting to the configured GPT master model);
- GPT implementation: one Codex implementation call only when the decision is actually `IMPLEMENT`;
- no GitHub mailbox commit or workflow is used merely to ask each adviser a question.

The trusted GitHub bridge is used only after GPT has produced a sanitised final decision that may need repository work.

## Persistence and audit

Local runtime state is stored under the learnerbot data directory in `master_change_council/` and mirrored in sanitised form to:

```text
/var/tmp/boot/master_change_council_latest.json
```

The trusted bridge publishes only the sanitised council record to:

```text
ai-reviews:master-change/requests/<request_id>.json
ai-reviews:master-change/latest_request.json
```

Implementation results are published to:

```text
ai-reviews:master-change/results/<request_id>.json
ai-reviews:master-change/latest_result.json
```

The Telegram AI watcher surfaces meaningful implementation state changes back to MASTER users.

## Failure behaviour

- Adviser does not ACK or provider call fails: `INCOMPLETE`; GPT implementation is blocked.
- GPT final call fails or malformed decision: `GPT_FAILED`/`FAILED`; implementation is blocked.
- `main` changed after council review: implementation workflow refuses stale evidence.
- GPT edits an unapproved path: workflow fails before commit/PR.
- Full test suite fails: no merge/deployment.
- Protected/core change: draft PR only.
- Low-risk merge blocked by repository protection: PR remains ready rather than bypassing protection.

## Relevant implementation files

- `learnerbot/master_change_council.py`
- `learnerbot/telegram_master_change_patch.py`
- `learnerbot/ai_agent_ws_runtime_patch.py`
- `.github/workflows/master-change-council-bridge.yml`
- `.github/workflows/gpt-master-change-implement.yml`
- `tests/test_master_change_council.py`
