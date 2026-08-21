# Gemini repository instructions

## Mandatory AI identity in GitHub-visible output

Follow `docs/AI_AGENT_IDENTITY.md`. Every Gemini-authored GitHub comment, issue body, PR body and human-readable report must begin with:

`🔵 AGENT: GEMINI`

AI-created PR titles must start with `[GEMINI]`. When known, include Role, Workflow/Task, Cycle and Source SHA directly below the identity header. AI-originated commit messages should include the trailer `AI-Agent: GEMINI`. Do not present Gemini work as GPT, Copilot or Claude work.

For every Engineering Audit / full-repository bug audit, read the deterministic baseline's `operational_efficiency_audit` section and include API/model cost, server bandwidth and disk-usage efficiency in the review.

Check for unnecessary paid model calls, duplicated AI cycles, excessive provider fan-out, oversized context, missing material-change/cache gates, repeated CLI/package installs, frequent self-hosted jobs, full-history checkouts, redundant Git/RPC/API polling, oversized logs/artifacts, runner-workspace growth, caches, databases and retention.

Use the sanitised VPS resource snapshot from `ai-reviews:engineering/ops/latest.json` when available. Its network counters measure host non-loopback traffic and must not be falsely attributed to the trading bot alone.

Recommendations are report-only and must never weaken wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation gates. Never automatically delete wallet material, databases or audit evidence.
