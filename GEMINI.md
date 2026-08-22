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

Gemini **can initiate** communication with GPT, Claude, DeepSeek and Copilot through the repository's universal AI mailbox. This is the normal mechanism for a new communication-only cross-agent message.

For a new cross-agent communication on branch `ai-mailbox`:

- Gemini may update **only** `.github/ai-mailbox/bus-from-gemini.md`;
- use `AI_BUS`, a unique `message_id`, `from: GEMINI`, one supported `to:` agent or `to: ALL`, `mode: DIRECT`, and `max_hops: 1`;
- valid direct targets from Gemini are `GPT`, `CLAUDE`, `DEEPSEEK`, and `COPILOT`; Gemini must not target itself;
- read only `.github/ai-mailbox/bus-to-gemini.md` for the result and require the same `message_id`;
- for several selected agents rather than `ALL`, send one message at a time and wait for its matching reply before overwriting the sender file.

Delivery is automatic and event-driven. A valid sender-mailbox push wakes the universal relay, which automatically invokes the addressed provider; `to: ALL` invokes every other supported provider once. The recipient does **not** poll a mailbox to discover new messages. The sender reads its correlated `bus-to-gemini.md` result after the relay completes.

### Exact send procedure for Gemini

Do not merely describe these steps. When the user asks Gemini to send a message and repository/GitHub write access is available, Gemini should perform them.

1. Read the current messaging contract:

   ```bash
   git fetch origin main ai-mailbox --quiet
   git show origin/main:AI_AGENT_MESSAGING.md
   ```

2. Work from the latest `ai-mailbox` state without disturbing the user's current engineering branch:

   ```bash
   MAILBOX_DIR="$(mktemp -d)"
   git worktree add --detach "$MAILBOX_DIR" origin/ai-mailbox
   cd "$MAILBOX_DIR"
   ```

3. Create a unique message ID. It should identify Gemini, the target, and time, for example:

   ```bash
   MESSAGE_ID="gemini-to-claude-$(date -u +%Y%m%dT%H%M%SZ)"
   ```

4. Write **only** `.github/ai-mailbox/bus-from-gemini.md` using this exact envelope, replacing the target and body as requested:

   ```text
   AI_BUS
   message_id: <unique-id>
   from: GEMINI
   to: <GPT|CLAUDE|DEEPSEEK|COPILOT|ALL>
   mode: DIRECT
   max_hops: 1

   <communication-only message>
   ```

5. Before committing, prove that no other mailbox or repository file was changed:

   ```bash
   git status --short
   test "$(git status --short | awk '{print $2}')" = ".github/ai-mailbox/bus-from-gemini.md"
   ```

   If more than that one path changed, stop and clean the worktree before sending.

6. Commit and push only that mailbox update:

   ```bash
   git add -- .github/ai-mailbox/bus-from-gemini.md
   git commit -m "ai-mailbox: Gemini message <unique-id>"
   git push origin HEAD:ai-mailbox
   ```

   If the push is rejected because `ai-mailbox` advanced, fetch the latest branch, replay only the single Gemini mailbox change on top of it, and retry. Do not force-push.

7. To retrieve the result, fetch `ai-mailbox` again and read only Gemini's reply file:

   ```bash
   git fetch origin ai-mailbox --quiet
   git show origin/ai-mailbox:.github/ai-mailbox/bus-to-gemini.md
   ```

   Accept the reply only if it starts with `AI_BUS_REPLY` and contains the exact same `message_id:`. A stale reply with a different ID is not the answer to the new message.

8. Report the delivery result precisely. If GitHub authentication, permissions, branch protection, or another concrete error prevents the push, report that exact error and the unsent `message_id`; do **not** make the false general claim that Gemini is unable to message other agents.

### Example send:

Gemini to Claude:

```text
AI_BUS
message_id: gemini-to-claude-20260822-001
from: GEMINI
to: CLAUDE
mode: DIRECT
max_hops: 1

Claude, please review this communication-only question.
```

Gemini to GPT:

```text
AI_BUS
message_id: gemini-to-gpt-20260822-001
from: GEMINI
to: GPT
mode: DIRECT
max_hops: 1

GPT, please give your independent view on this issue.
```

Gemini to DeepSeek:

```text
AI_BUS
message_id: gemini-to-deepseek-20260822-001
from: GEMINI
to: DEEPSEEK
mode: DIRECT
max_hops: 1

DeepSeek, please review this reasoning and identify any weaknesses.
```

Gemini to Copilot:

```text
AI_BUS
message_id: gemini-to-copilot-20260822-001
from: GEMINI
to: COPILOT
mode: DIRECT
max_hops: 1

Copilot, please review the technical point below.
```

Gemini to all other agents:

```text
AI_BUS
message_id: gemini-to-all-20260822-001
from: GEMINI
to: ALL
mode: DIRECT
max_hops: 1

Please give your independent view on this communication-only question.
```

This is a communication-only mailbox exception. It grants no authority to edit any other `ai-mailbox` path or to deploy, trade, change LIVE/ARMED or risk/capital settings, access wallets/signing material or secrets, or use root/sudo.

## Engineering audit operational-efficiency requirement

For every Engineering Audit / full-repository bug audit, read the deterministic baseline's `operational_efficiency_audit` section and include API/model cost, server bandwidth and disk-usage efficiency in the review.

Check for unnecessary paid model calls, duplicated AI cycles, excessive provider fan-out, oversized context, missing material-change/cache gates, repeated CLI/package installs, frequent self-hosted jobs, full-history checkouts, redundant Git/RPC/API polling, oversized logs/artifacts, runner-workspace growth, caches, databases and retention.

Use the sanitised VPS resource snapshot from `ai-reviews:engineering/ops/latest.json` when available. Its network counters measure host non-loopback traffic and must not be falsely attributed to the trading bot alone.

Recommendations are report-only and must never weaken wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation gates. Never automatically delete wallet material, databases or audit evidence.