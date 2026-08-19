# Weekly Three-Agent Full-Bot Bug Audit

## Purpose

Once per week, the repository freezes the current `main` commit and asks three independent agents — GPT, Gemini and GitHub Copilot — to audit the **full bot** for software defects. This is separate from the hourly trading/strategy review.

The weekly audit targets correctness and reliability defects across EVM, Solana, accounting/P&L, databases, concurrency, retries/timeouts, Telegram/operator permissions, Strategy Lab/shadow systems, configuration, tests, GitHub workflows and deployment interactions.

The audit is deliberately conservative: the first phase is report-only. No agent is allowed to trade, deploy, change capital, edit credentials/signing material, enable live modes or weaken execution/risk controls.

## Schedule

The kickoff workflow is `.github/workflows/weekly-three-agent-bug-audit.yml`.

- Scheduled: **Monday 04:15 UTC** (`15 4 * * 1`).
- Manual: GitHub Actions → **Weekly Three-Agent Full-Bot Bug Audit** → **Run workflow**.
- Every run records one exact `main` source commit. All three reports must name that same source commit.

## Stage A — independent reports

The kickoff workflow performs a deterministic repository baseline first:

- Python compilation;
- full pytest baseline;
- tracked-file inventory;
- Python AST syntax/duplicate-symbol checks;
- broad exception-handler inventory;
- selected static bug-risk patterns;
- GitHub workflow inventory.

It then runs GPT and Gemini independently against the same checked-out source. Their reports are validated against the common report contract and are not allowed to change tracked code.

The workflow creates a report-only GitHub issue assigned to `@copilot`. Copilot is instructed to audit the same frozen commit and open a PR containing **only**:

```text
.ai/weekly/copilot/<SOURCE_COMMIT>.json
.ai/weekly/copilot/<SOURCE_COMMIT>.md
```

Anything else in that Copilot report PR causes Stage B to refuse processing.

## Report locations

The durable audit record is stored on the `ai-reviews` branch.

For a source commit `<SHA>`:

```text
weekly/runs/<SHA>/baseline.json
weekly/runs/<SHA>/gpt.json
weekly/runs/<SHA>/gpt.md
weekly/runs/<SHA>/gemini.json
weekly/runs/<SHA>/gemini.md
weekly/runs/<SHA>/copilot_assignment.json
weekly/runs/<SHA>/copilot.json
weekly/runs/<SHA>/copilot.md
weekly/runs/<SHA>/master_decision_raw.json
weekly/runs/<SHA>/master_decision.json
weekly/runs/<SHA>/master_decision.md
weekly/runs/<SHA>/completion.json
```

Convenience pointers:

```text
weekly/latest_source_commit.txt
weekly/latest_kickoff_utc.txt
weekly/latest_completed_source_commit.txt
weekly/latest_completed_utc.txt
weekly/latest_master_decision.json
```

`master_decision_raw.json` is GPT's original adjudication. `master_decision.json` is the same decision after the deterministic safety/policy gate has been applied. **Use the gated file as the authoritative automatic-action record.**

## Stage B — GPT master adjudication

The workflow `.github/workflows/weekly-gpt-corrective-action.yml` starts when the same-repository Copilot report PR is opened or updated, or can be run manually with the report PR number.

It refuses to continue unless:

1. the Copilot PR contains exactly the two requested report files;
2. the Copilot JSON passes the report schema;
3. GPT, Gemini and Copilot all report on the exact same source commit;
4. none of the three reports is `INCOMPLETE`;
5. the source commit is still in current `main` history.

GPT then reads all three independent reports, the deterministic baseline, and the current code. It must create a decision for every materially distinct proposed defect.

### GPT disposition meanings

- **ACCEPT** — GPT verified enough evidence to recommend a bounded corrective action.
- **REJECT** — GPT believes the proposal is wrong, stale, duplicated, speculative, strategy tuning rather than a software bug, or contradicted by code/tests.
- **DEFER** — the issue may be real but needs more runtime evidence, reproduction, human judgement or a safer design before code should change.

Each decision records the reason, source finding IDs, severity, confidence, supporting agents, whether deterministic evidence exists, risk class, exact allowed files and required verification tests.

This ledger is how an operator can see **what GPT agreed to do, what GPT refused to do, and why**.

## Independent policy gate — GPT cannot self-authorise

After GPT writes its master decision, `learnerbot/weekly_bug_audit_contract.py` applies a deterministic gate.

An accepted fix is automatically eligible for an implementation attempt only when all relevant policy requirements pass:

- GPT disposition is `ACCEPT`;
- confidence is at least `0.85`;
- at least two independent agents support the root cause, **or** the finding is explicitly deterministic;
- deterministic single-agent evidence has an explicit verification test;
- risk class is only `LOW` or `MEDIUM`;
- severity is not `P0`;
- the fix has an exact bounded file allow-list;
- the fix has explicit required tests;
- no protected/high-risk file is in the allow-list.

P0 findings, `HIGH`/`CRITICAL` risk changes and protected-file changes become `HUMAN_REVIEW_REQUIRED`. They cannot be automatically implemented even if GPT wants them.

## Stage C — bounded corrective implementation

If at least one decision survives the deterministic gate, GPT/Codex may attempt the fix on an isolated branch based on current `main`.

The implementation agent is constrained to the exact files in policy-eligible `allowed_files`. A second deterministic check compares the actual Git diff and untracked files against that allow-list. Any extra file fails the workflow.

Before a patch can be pushed, the workflow runs:

- Python compileall;
- full pytest;
- critical import-order and Solana LIVE safety regression tests;
- SiBot/profit-control/audit safety tests;
- weekly audit policy tests.

A passing patch is pushed to an `ai/weekly-fix-*` branch and GitHub opens a **draft pull request only**.

The system never auto-merges that PR and never authorises deployment from the weekly audit workflow.

## How to judge whether GPT's decision is trustworthy

Do not treat model agreement by itself as proof. The weekly design uses several independent checks:

1. Three agents inspect the same frozen source independently.
2. Deterministic compile/test/static evidence is captured separately from model opinion.
3. GPT must explain every `ACCEPT`, `REJECT` and `DEFER` decision against code/test evidence.
4. A deterministic policy gate can override GPT and block action.
5. The implementation agent is restricted to exact approved files.
6. Full regression/safety tests must pass before a draft PR can be created.
7. The draft PR still requires human review before merge.

For important or disputed changes, inspect `master_decision.json`, then the corrective draft PR's actual diff and CI results. A green audit is evidence of process completion; it is not a guarantee that no software defect exists.

## Failure behaviour

The system fails closed:

- missing GPT/Gemini credentials → that agent becomes `INCOMPLETE`;
- Copilot assignment failure → the kickoff record says `COPILOT_ASSIGNMENT_NEEDS_ATTENTION`;
- any incomplete independent report → GPT automatic corrective action is blocked;
- unexpected Copilot PR files → blocked;
- source commit no longer in `main` history → blocked;
- GPT synthesis changes files during its report phase → blocked;
- high/critical risk or protected path → human review;
- implementation changes an unapproved file → blocked;
- mandatory tests fail → no corrective PR is created.
