# Claude repository instructions

## Strategy Room mailbox awareness

At the start of every persistent/interactive Claude repository session, and before claiming GPT has not sent a message, fetch `origin/ai-mailbox` when Git access is available and inspect `.github/ai-mailbox/gpt-to-claude.md`. Treat a new unmatched `message_id`/`in_reply_to` as an unread Strategy Room message. This is the persistent agent's inbox check; GitHub cannot inject a UI push into an already-open external Claude session.

Identity is strict: `.github/ai-mailbox/claude-to-gpt.md` is reserved for messages authored by the persistent/interactive Claude agent. `.github/ai-mailbox/claude-api-to-gpt.md` is reserved for the stateless Anthropic API responder and must never be described as a message authored by this persistent Claude session. If both exist, keep their identities separate in every report.

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
6. **Branch-only workflow remains mandatory.** Unless the user explicitly changes this rule, Claude may commit and push only its feature branch. Do not merge, rebase onto, force-push, or push directly to `main` merely because a handoff exists. The dedicated `ai-mailbox` exception below permits only the fixed Claude-to-GPT mailbox file to be updated for communication.
7. **No silent override of safety controls.** A handoff never authorises weakening wallet/signing, LIVE/ARMED, quote/simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce, execution-reconciliation, secrets, or other safety controls unless the user explicitly requests that specific change.

The shared handoff file is `.github/claude-handoff.md`. ChatGPT may update that file to pass current review results, stop instructions, branch decisions, deployment status, or the next bounded task to Claude.

## Claude → GPT git-only mailbox

If the current Claude environment has working Git fetch/push but does **not** have `gh`, a GitHub API token, browser authentication, or a GitHub connector, use the dedicated `ai-mailbox` branch. Do not claim issue comments are required.

To send GPT a message:

1. Fetch `origin/ai-mailbox`.
2. Update **only** `.github/ai-mailbox/claude-to-gpt.md` on the `ai-mailbox` branch.
3. The file must begin with `CLAUDE_TO_GPT` and include a unique `message_id:` header. Include `source_sha:`, `status:`, `constraints:`, and the bounded message/evidence as appropriate.
4. Commit that mailbox-file change and push it to `ai-mailbox` with ordinary Git. This mailbox-only push is the sole exception to the normal feature-branch-only rule.
5. Do not modify code, workflow files, configuration, trading/runtime files, or any other path on `ai-mailbox` merely to send a message.
6. The event-driven mailbox signal wakes the trusted bridge only when `claude-to-gpt.md` changes. The bridge first deduplicates by `message_id`; only a new message invokes GPT. There is no scheduled provider polling for this Claude channel.
7. Read GPT's reply after `git fetch origin ai-mailbox` from `.github/ai-mailbox/gpt-to-claude.md`. Match the `in_reply_to:` value to the `message_id` you sent.

Never include secrets, API keys, private keys, mnemonics, signing material, wallet credentials, or other secret values in either mailbox file.

## Engineering audit operational-efficiency requirement

Whenever Claude performs the repository's Engineering Audit / full-bot bug audit, it must also review the `operational_efficiency_audit` object in the deterministic baseline and, when available, the sanitised VPS snapshot at `ai-reviews:engineering/ops/latest.json`.

Every completed engineering audit must explicitly assess:

1. **API/model cost** — unnecessary paid OpenAI/Gemini/Claude/Copilot calls, duplicate reviews/adjudication, excessive provider fan-out, oversized prompts/context, unnecessary CLI installs, missing cache/material-change gates, and opportunities to use deterministic checks before paid AI.
2. **Server bandwidth** — self-hosted workflow frequency, repository checkouts/fetches, repeated npm/pip downloads, RPC/API polling, logs/artifacts, redundant jobs and other avoidable transfers. Prefer event-driven, shallow, cached and change-only transfers.
3. **Disk usage** — root-filesystem utilisation/free space, runner workspaces, caches, logs, databases, temporary worktrees/artifacts and unbounded retention/growth.

Recommendations are advisory/report-only. Never reduce API or bandwidth cost by weakening wallet/signing, LIVE/ARMED, quote/simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation protections. Never automatically delete wallet material, databases or audit evidence.