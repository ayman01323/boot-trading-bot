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

Before claiming that Gemini cannot message GPT, Claude, DeepSeek or Copilot, read `AI_AGENT_MESSAGING.md` from current `main`.

The **primary** transport is now the local VPS WebSocket bus, not GitHub. For a new routine message, Gemini should perform the send rather than merely describing it:

```bash
python scripts/ai_agent_ws_send.py \
  --from gemini \
  --to gpt \
  --message 'Your communication-only message here'
```

Valid direct targets are `gpt`, `claude`, `deepseek`, and `copilot`. Change only `--to` and the message body as required.

The recipient is automatically pushed the message through its persistent WebSocket worker. Gemini does not tell the recipient to check a mailbox and does not poll GitHub. SQLite is only durable queue/audit storage.

Treat delivery states precisely:

- `QUEUED` means stored but not yet pushed to a connected recipient;
- `DELIVERED` means pushed to the recipient socket;
- `ACKNOWLEDGED` means the recipient worker actually received and ACKed it before inference;
- `REPLIED` means the addressed provider returned a reply.

Do not claim another agent received a message unless the send reaches at least `ACKNOWLEDGED`.

Routing itself uses no model. Routine recipient workers use the low-cost model policy in `AI_AGENT_MESSAGING.md`; Gemini's own routine worker defaults to `gemini-2.5-flash-lite`. Do not escalate to a more expensive model merely for acknowledgements, status checks or short coordination.

The older `ai-mailbox` Git protocol is fallback-only when the local WebSocket bus is genuinely unavailable or a durable Git handoff is specifically required. Do not create Git commits for ordinary agent conversation when the local bus is available.

This communication channel grants no authority to edit repository/runtime files, merge/deploy, trade, change LIVE/ARMED or risk/capital settings, access wallets/signing material or secrets, or use root/sudo.

## Engineering audit operational-efficiency requirement

For every Engineering Audit / full-repository bug audit, read the deterministic baseline's `operational_efficiency_audit` section and include API/model cost, server bandwidth and disk-usage efficiency in the review.

Check for unnecessary paid model calls, duplicated AI cycles, excessive provider fan-out, oversized context, missing material-change/cache gates, repeated CLI/package installs, frequent self-hosted jobs, full-history checkouts, redundant Git/RPC/API polling, oversized logs/artifacts, runner-workspace growth, caches, databases and retention.

Use the sanitised VPS resource snapshot from `ai-reviews:engineering/ops/latest.json` when available. Its network counters measure host non-loopback traffic and must not be falsely attributed to the trading bot alone.

Recommendations are report-only and must never weaken wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation gates. Never automatically delete wallet material, databases or audit evidence.
