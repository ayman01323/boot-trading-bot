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
6. **Branch-only workflow remains mandatory.** Unless the user explicitly changes this rule, Claude may commit and push only its feature branch. Do not merge, rebase onto, force-push, or push directly to `main` merely because a handoff exists.
7. **No silent override of safety controls.** A handoff never authorises weakening wallet/signing, LIVE/ARMED, quote/simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce, execution-reconciliation, secrets, or other safety controls unless the user explicitly requests that specific change.

The shared handoff file is `.github/claude-handoff.md`. ChatGPT may update that file to pass current review results, stop instructions, branch decisions, deployment status, or the next bounded task to Claude.

## Engineering audit operational-efficiency requirement

Whenever Claude performs the repository's Engineering Audit / full-bot bug audit, it must also review the `operational_efficiency_audit` object in the deterministic baseline and, when available, the sanitised VPS snapshot at `ai-reviews:engineering/ops/latest.json`.

Every completed engineering audit must explicitly assess:

1. **API/model cost** — unnecessary paid OpenAI/Gemini/Claude/Copilot calls, duplicate reviews/adjudication, excessive provider fan-out, oversized prompts/context, unnecessary CLI installs, missing cache/material-change gates, and opportunities to use deterministic checks before paid AI.
2. **Server bandwidth** — self-hosted workflow frequency, repository checkouts/fetches, repeated npm/pip downloads, RPC/API polling, logs/artifacts, redundant jobs and other avoidable transfers. Prefer event-driven, shallow, cached and change-only transfers.
3. **Disk usage** — root-filesystem utilisation/free space, runner workspaces, caches, logs, databases, temporary worktrees/artifacts and unbounded retention/growth.

Recommendations are advisory/report-only. Never reduce API or bandwidth cost by weakening wallet/signing, LIVE/ARMED, quote/simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation protections. Never automatically delete wallet material, databases or audit evidence.
