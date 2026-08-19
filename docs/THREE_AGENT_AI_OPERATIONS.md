# Three-Agent AI Operations

## Sequence

1. **Engineering first** — GPT, Gemini and Copilot independently audit the same frozen source commit.
2. **GPT engineering adjudication** — every distinct finding is recorded as `ACCEPT`, `REJECT` or `DEFER`.
3. **Deterministic engineering gate** — GPT cannot override the hard policy. High-risk/protected/low-confidence/unsupported changes are blocked. Eligible fixes may only become a tested draft PR.
4. **Strategy second** — only after the engineering master workflow completes successfully, GPT, Gemini and Copilot independently review the same strategy source and sanitised Strategy Lab evidence.
5. **GPT strategy adjudication** — every distinct proposal is recorded as `ACCEPT`, `REJECT` or `DEFER`.
6. **Deterministic Strategy Lab gate** — automatic code changes are restricted to low/medium-risk SHADOW Strategy Lab files and require two independent supporting agents, confidence >= 0.85, an exact file allow-list and explicit tests.
7. **Tests + draft PR only** — no AI workflow auto-merges or auto-deploys.

## MASTER Telegram access

The VPS reads ACTIVE `role=MASTER` accounts from `CSVbot/users.csv`; it does not maintain a separate AI-admin list.

MASTER-only commands:

- `/aiaudit` — latest engineering cycle and GPT/Gemini/Copilot completion state.
- `/aidecision` — GPT engineering ACCEPT/REJECT/DEFER ledger and policy overrides.
- `/aidecision accept`, `/aidecision reject`, `/aidecision defer` — filtered engineering decisions.
- `/aistrategy` — latest three-agent strategy cycle, master decision and draft strategy PR state.
- `/aiupdates` — combined engineering + strategy status.

The background watcher sends MASTERs a message only on meaningful state transitions: audit start, new agent completion, all-three completion, GPT master decision, corrective draft PR, strategy-cycle start/completion and strategy draft PR. It reads only the sanitised `ai-reviews` branch and never reads wallet keys or signing material.

## How to prove all three agents reported

Engineering reports are stored under:

`ai-reviews:weekly/runs/<SOURCE_COMMIT>/`

A valid completion has `gpt.json`, `gemini.json`, `copilot.json` and `completion.json` with `three_agent_reports_complete=true`. `master_decision.json` is not produced until all three reports are present and complete.

Strategy reports are stored under:

`ai-reviews:strategy/runs/<CYCLE_ID>/`

`strategy/latest_status.json` exposes GPT/Gemini/Copilot state. After all three complete it is updated with `three_agent_reports_complete=true` and the GPT master ledger is copied to `strategy/latest_master_decision.json`.

## How to judge GPT's decision

GPT is an adjudicator, not an unquestionable authority. The system reduces decision risk by requiring:

- the same source commit and same evidence for all independent reviewers;
- explicit evidence paths for every bug/proposal;
- an ACCEPT/REJECT/DEFER reason for every consolidated item;
- objective measured evidence over majority voting;
- deterministic policy gates after GPT;
- minimum confidence and independent-agent support;
- exact permitted-file scopes and explicit tests;
- high-risk/protected/live execution decisions to remain human-only;
- full regression tests before a draft code PR;
- no automatic merge or deployment.

No multi-agent system can guarantee a decision is correct. The final proof for strategy changes is out-of-sample SHADOW evidence and, only after separate human approval, any later bounded canary/live evidence.
