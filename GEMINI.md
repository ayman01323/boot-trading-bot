# Gemini repository instructions

## Mandatory ChatGPT ↔ Gemini handoff protocol

Before Gemini analyses, edits, tests, commits, or pushes in this repository:

1. Run `git fetch origin main --quiet` and read `git show origin/main:.github/gemini-handoff.md`.
2. If it contains a `handoff_id`, the first substantive response must include exactly:

   `GEMINI_HANDOFF_ACK: <handoff_id>`

3. Follow the handoff's `status`, `scope`, `do_not_do`, and `message` fields. A newer handoff supersedes an older one.
4. If current `origin/main` or `.github/gemini-handoff.md` cannot be read, stop before repository changes and report `GEMINI_HANDOFF_READ_FAILED`.
5. Re-fetch and re-read the handoff immediately before any push. If the ID changed, acknowledge and follow the newer handoff first.
6. A handoff never authorises merge/deploy/trading or weakening wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce, execution-reconciliation, secrets, or other safety controls unless the user explicitly requests that specific change.

The shared Gemini inbox is `.github/gemini-handoff.md`.

## Engineering audit operational-efficiency requirement

For every Engineering Audit / full-repository bug audit, read the deterministic baseline's `operational_efficiency_audit` section and include API/model cost, server bandwidth and disk-usage efficiency in the review.

Check for unnecessary paid model calls, duplicated AI cycles, excessive provider fan-out, oversized context, missing material-change/cache gates, repeated CLI/package installs, frequent self-hosted jobs, full-history checkouts, redundant Git/RPC/API polling, oversized logs/artifacts, runner-workspace growth, caches, databases and retention.

Use the sanitised VPS resource snapshot from `ai-reviews:engineering/ops/latest.json` when available. Its network counters measure host non-loopback traffic and must not be falsely attributed to the trading bot alone.

Recommendations are report-only and must never weaken wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation gates. Never automatically delete wallet material, databases or audit evidence.
