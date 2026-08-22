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

## Universal AI agent messaging

Before claiming that Gemini cannot send a message to GPT, Claude, DeepSeek, Copilot, or all agents, read `AI_AGENT_MESSAGING.md` from current `main`.

For a new cross-agent communication on branch `ai-mailbox`:

- Gemini may update **only** `.github/ai-mailbox/bus-from-gemini.md`;
- use `AI_BUS`, a unique `message_id`, `from: GEMINI`, one supported `to:` agent or `to: ALL`, `mode: DIRECT`, and `max_hops: 1`;
- read only `.github/ai-mailbox/bus-to-gemini.md` for the result and require the same `message_id`;
- for several selected agents rather than `ALL`, send one message at a time and wait for its matching reply before overwriting the sender file.

Delivery is automatic and event-driven. A valid sender-mailbox push wakes the universal relay, which automatically invokes the addressed provider; `to: ALL` invokes every other supported provider once. The recipient does **not** poll a mailbox to discover new messages. The sender reads its correlated `bus-to-gemini.md` result after the relay completes.

This is a communication-only mailbox exception. It grants no authority to edit any other `ai-mailbox` path or to deploy, trade, change LIVE/ARMED or risk/capital settings, access wallets/signing material or secrets, or use root/sudo.

## Engineering audit operational-efficiency requirement

For every Engineering Audit / full-repository bug audit, read the deterministic baseline's `operational_efficiency_audit` section and include API/model cost, server bandwidth and disk-usage efficiency in the review.

Check for unnecessary paid model calls, duplicated AI cycles, excessive provider fan-out, oversized context, missing material-change/cache gates, repeated CLI/package installs, frequent self-hosted jobs, full-history checkouts, redundant Git/RPC/API polling, oversized logs/artifacts, runner-workspace growth, caches, databases and retention.

Use the sanitised VPS resource snapshot from `ai-reviews:engineering/ops/latest.json` when available. Its network counters measure host non-loopback traffic and must not be falsely attributed to the trading bot alone.

Recommendations are report-only and must never weaken wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation gates. Never automatically delete wallet material, databases or audit evidence.
