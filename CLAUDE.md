# Claude repository instructions

## Agent identity routing

These instructions are normally for Claude. However, some repository workflows run the `claude` CLI against the DeepSeek API. If the explicit task/prompt identifies the acting agent as **DEEPSEEK**, do **not** use the Claude handoff inbox. Instead:

- fetch current `origin/main`;
- read `.github/deepseek-handoff.md` from `origin/main`;
- acknowledge `DEEPSEEK_HANDOFF_ACK: <handoff_id>`;
- follow the DeepSeek handoff's `status`, `scope`, `do_not_do`, and `message` fields;
- fail closed with `DEEPSEEK_HANDOFF_READ_FAILED` if the current handoff cannot be read;
- re-read the DeepSeek handoff immediately before any push.

When the task identifies the acting agent as Claude, use the Claude protocol below.

## Mandatory ChatGPT ↔ Claude handoff protocol

This protocol applies to every Claude Code session and every task in this repository, before analysis, edits, tests, commits, or pushes.

1. **Fetch the current handoff source first.** Run `git fetch origin main --quiet` and read the current handoff with `git show origin/main:.github/claude-handoff.md`. Do not rely on a stale copy from the current feature branch.
2. **Acknowledge the handoff before doing work.** If the handoff contains a `handoff_id`, the first substantive response must include this exact line:

   `CLAUDE_HANDOFF_ACK: <handoff_id>`

   Replace `<handoff_id>` with the exact value from the file.
3. **Treat the handoff as current coordination state.** Follow its `status`, `scope`, `do_not_do`, and `message` fields. A newer handoff supersedes an older one.
4. **Fail closed if the handoff cannot be read.** If `origin/main` cannot be fetched or `.github/claude-handoff.md` cannot be read, stop before making repository changes and report:

   `CLAUDE_HANDOFF_READ_FAILED`
5. **Re-read before pushing.** Immediately before any `git push`, fetch `origin/main` again and re-read `.github/claude-handoff.md`. If the `handoff_id` changed, acknowledge the new ID and follow the newer instructions before pushing.
6. **Branch-only workflow remains mandatory.** Unless the user explicitly changes this rule, Claude may commit and push only its feature branch. Do not merge, rebase onto, force-push, or push directly to `main` merely because a handoff exists. The dedicated communication-only `ai-mailbox` exceptions below permit only the fixed Claude mailbox files named in those protocols to be updated.
7. **No silent override of safety controls.** A handoff never authorises weakening wallet/signing, LIVE/ARMED, quote/simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce, execution-reconciliation, secrets, or other safety controls unless the user explicitly requests that specific change.

The shared handoff file is `.github/claude-handoff.md`. ChatGPT may update that file to pass current review results, stop instructions, branch decisions, deployment status, or the next bounded task to Claude.

## Claude → GPT git-only mailbox

If the current Claude environment has working Git fetch/push but does **not** have `gh`, a GitHub API token, browser authentication, or a GitHub connector, use the dedicated `ai-mailbox` branch. Do not claim issue comments are required.

To send GPT a message:

1. Fetch `origin/ai-mailbox`.
2. Update **only** `.github/ai-mailbox/claude-to-gpt.md` on the `ai-mailbox` branch.
3. The file must begin with `CLAUDE_TO_GPT` and include a unique `message_id:` header. Include `source_sha:`, `status:`, `constraints:`, and the bounded message/evidence as appropriate.
4. Commit that mailbox-file change and push it to `ai-mailbox` with ordinary Git. This is a communication-only exception to the normal feature-branch-only rule.
5. Do not modify code, workflow files, configuration, trading/runtime files, or any other path on `ai-mailbox` merely to send a message.
6. The event-driven mailbox signal wakes the trusted bridge only when `claude-to-gpt.md` changes. The bridge first deduplicates by `message_id`; only a new message invokes GPT. There is no scheduled provider polling for this Claude channel.
7. Read GPT's reply after `git fetch origin ai-mailbox` from `.github/ai-mailbox/gpt-to-claude.md`. Match the `in_reply_to:` value to the `message_id` you sent.

Never include secrets, API keys, private keys, mnemonics, signing material, wallet credentials, or other secret values in either mailbox file.

## Universal AI agent messaging

Before claiming that Claude cannot send a message to GPT, Gemini, DeepSeek, Copilot, or all agents, read `AI_AGENT_MESSAGING.md` from current `main`.

For a new cross-agent communication, Claude may use the universal communication-only mailbox exception:

- write **only** `.github/ai-mailbox/bus-from-claude.md` on branch `ai-mailbox`;
- use an `AI_BUS` message with `from: CLAUDE`, one `to:` target or `to: ALL`, `mode: DIRECT`, `max_hops: 1`, and a unique `message_id`;
- read the matching result only from `.github/ai-mailbox/bus-to-claude.md` and require the same `message_id`;
- for several selected agents rather than `ALL`, send one message at a time and wait for its matching reply before overwriting the sender file.

Delivery is automatic and event-driven. A valid sender-mailbox push wakes the universal relay, which automatically invokes the addressed provider; `to: ALL` invokes every other supported provider once. The recipient does **not** poll a mailbox to discover new messages. The sender reads its correlated `bus-to-claude.md` result after the relay completes.

Example send:

```text
AI_BUS
message_id: claude-to-gemini-20260822-001
from: CLAUDE
to: GEMINI
mode: DIRECT
max_hops: 1

Gemini, please review this communication-only question.
```

This exception grants no authority to modify any other `ai-mailbox` path. The universal bus is communication-only and never authorises deploy, trading, LIVE/ARMED, risk/capital, wallet/signing, secret, root, or sudo actions.

## Engineering audit operational-efficiency requirement

Whenever Claude or a DeepSeek-routed Claude CLI performs the repository's Engineering Audit / full-bot bug audit, it must also review the `operational_efficiency_audit` object in the deterministic baseline and, when available, the sanitised VPS snapshot at `ai-reviews:engineering/ops/latest.json`.

Every completed engineering audit must explicitly assess:

1. **API/model cost** — unnecessary paid OpenAI/Gemini/Claude/Copilot calls, duplicate reviews/adjudication, excessive provider fan-out, oversized prompts/context, unnecessary CLI installs, missing cache/material-change gates, and opportunities to use deterministic checks before paid AI.
2. **Server bandwidth** — self-hosted workflow frequency, repository checkouts/fetches, repeated npm/pip downloads, RPC/API polling, logs/artifacts, redundant jobs and other avoidable transfers. Prefer event-driven, shallow, cached and change-only transfers.
3. **Disk usage** — root-filesystem utilisation/free space, runner workspaces, caches, logs, databases, temporary worktrees/artifacts and unbounded retention/growth.
4. **Trade latency and infrastructure economics** — the snapshot's `trade_latency` and `infrastructure` objects must be analysed chain by chain.

For each blockchain with observed trades, state the exact latency metric and coverage, per-trade latency evidence where available, current-24h p50/p95 against the preceding six-day same-server baseline, RPC round-trip separately, and seven-day trade share. The preceding six days are the default measured **normal** comparison. If there are fewer than five historical measurements, state `INSUFFICIENT DATA`; never fabricate a normal value or use expected protocol block time as if it were measured trade latency.

Only attribute worse success, slippage or realised P&L to latency when recorded evidence supports that inference. End the infrastructure section with `KEEP`, `BENCHMARK`, or `MOVE`. Compare current provider, region and monthly cost with each credible alternative's provider, region, monthly cost, expected latency improvement, affected trade share and likely trading benefit. Keep unknown facts `UNKNOWN`. Alternative pricing must have a source/date or be marked unverified.

Do not recommend a server move merely from ping. Require a cost-adjusted, chain-weighted trading case. If Solana and EVM materially favour different locations, analyse split infrastructure or chain-specific workers before recommending a wholesale migration.

Recommendations are advisory/report-only. Never reduce API, bandwidth or infrastructure cost by weakening wallet/signing, LIVE/ARMED, quote/simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation protections. Never automatically delete wallet material, databases or audit evidence.
