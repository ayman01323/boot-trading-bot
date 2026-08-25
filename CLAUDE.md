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
6. **Branch-only workflow remains mandatory.** Unless the user explicitly changes this rule, Claude may commit and push only its feature branch. Do not merge, rebase onto, force-push, or push directly to `main` merely because a handoff exists. Communication over the local Strategy Factory WebSocket does not grant repository-write authority. The dedicated `ai-mailbox` exception below is fallback/audit only and permits only the fixed mailbox files named there.
7. **No silent override of safety controls.** A handoff never authorises weakening wallet/signing, LIVE/ARMED, quote/simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce, execution-reconciliation, secrets, or other safety controls unless the user explicitly requests that specific change.

The shared handoff file is `.github/claude-handoff.md`. ChatGPT may update that file to pass current review results, stop instructions, branch decisions, deployment status, or the next bounded task to Claude.

## Primary Claude communication: persistent Strategy Factory WebSocket

Normal Claude communication with GPT, Gemini, DeepSeek, Grok, Kimi, Copilot, or MASTER must use the persistent Strategy Factory WebSocket transport described in `AI_AGENT_MESSAGING.md` whenever the local bus is reachable.

The canonical local endpoint is `ws://127.0.0.1:8765`. The durable queue, audit record and bounded conversation memory are stored in `/var/tmp/boot/ai_agent_bus.sqlite3`.

Before claiming that Claude has no message, no context, or cannot communicate with another Strategy Factory agent:

1. Read current `AI_AGENT_MESSAGING.md` from `origin/main`.
2. Prefer `scripts/strategy_factory_chat.py` for MASTER-to-Claude chat and `scripts/ai_agent_ws_send.py` for agent-to-agent communication.
3. For Claude-to-GPT communication use, for example:

   ```bash
   python scripts/ai_agent_ws_send.py --from claude --to gpt --message 'Your message'
   ```

4. Preserve `subject`/`thread_id` for continuing work so Claude receives the correct bounded subject history rather than a stateless one-off prompt.
5. Require correlated delivery evidence. Do not claim receipt below `ACKNOWLEDGED`; do not claim a deterministic task completed below `COMPLETED`.
6. Communication messages are advisory only. If a bounded read-only repository task is needed, use the allow-listed `ws-bus-v2` task envelope. Repository mutation, deployment, trading, LIVE/ARMED, risk/capital, wallet/signing and secret operations remain outside ordinary messaging authority.

The Strategy Factory Claude worker is the canonical persistent Claude communication identity. A separate Claude Web/browser chat and the legacy Anthropic/Git mailbox bridge are not the same session and must not be described as if they share Strategy Factory conversation memory.

## Git mailbox fallback / durable audit handoff

Use the dedicated `ai-mailbox` branch only when the local Strategy Factory WebSocket is genuinely unavailable, the current environment cannot reach it, or a durable Git handoff is specifically required. Do **not** choose the Git mailbox merely because Git is available; it is not the primary communication transport and its provider bridge is stateless relative to Strategy Factory subject-thread memory.

For Claude → GPT fallback:

1. Fetch `origin/ai-mailbox`.
2. Update **only** `.github/ai-mailbox/claude-to-gpt.md` on the `ai-mailbox` branch.
3. The file must begin with `CLAUDE_TO_GPT` and include a unique `message_id:` header. Include `source_sha:`, `status:`, `constraints:`, and the bounded message/evidence as appropriate.
4. Commit that mailbox-file change and push it to `ai-mailbox` with ordinary Git. This is a communication-only exception to the normal feature-branch-only rule.
5. Do not modify code, workflow files, configuration, trading/runtime files, or any other path on `ai-mailbox` merely to send a message.
6. The event-driven mailbox signal wakes the trusted bridge only when `claude-to-gpt.md` changes. The bridge deduplicates by `message_id`; only a new message invokes GPT. There is no scheduled provider polling for this fallback channel.
7. Read GPT's reply after `git fetch origin ai-mailbox` from `.github/ai-mailbox/gpt-to-claude.md`. Match the `in_reply_to:` value to the `message_id` you sent.

For fallback communication with agents other than GPT, use only the fixed sender/recipient mailbox paths documented in `AI_AGENT_MESSAGING.md` for the current repository version. Do not treat a Git mailbox commit as proof that the recipient acknowledged or replied.

Never include secrets, API keys, private keys, mnemonics, signing material, wallet credentials, or other secret values in WebSocket message bodies or mailbox files.

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