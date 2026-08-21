# Claude repository instructions

## Mandatory AI identity in GitHub-visible output

Follow `docs/AI_AGENT_IDENTITY.md`. Every Claude-authored GitHub comment, issue body, PR body and human-readable report must begin with:

`🟣 AGENT: CLAUDE`

AI-created PR titles must start with `[CLAUDE]`. When known, include Role, Workflow/Task, Cycle and Source SHA directly below the identity header. AI-originated commit messages should include the trailer `AI-Agent: CLAUDE`, and use Claude as the commit author when the execution environment safely supports it. Do not present Claude work as GPT, Gemini, Copilot or DeepSeek work.

If Claude Code is being used only as a transport/read-only harness for a non-Claude provider such as DeepSeek, the actual model/provider identity wins: use that provider's identity header, not `CLAUDE`.

## Engineering audit operational-efficiency requirement

Whenever Claude performs the repository's Engineering Audit / full-bot bug audit, it must also review the `operational_efficiency_audit` object in the deterministic baseline and, when available, the sanitised VPS snapshot at `ai-reviews:engineering/ops/latest.json`.

Every completed engineering audit must explicitly assess:

1. **API/model cost** — unnecessary paid OpenAI/Gemini/Claude/Copilot/DeepSeek calls, duplicate reviews/adjudication, excessive provider fan-out, oversized prompts/context, unnecessary CLI installs, missing cache/material-change gates, and opportunities to use deterministic checks before paid AI.
2. **Server bandwidth** — self-hosted workflow frequency, repository checkouts/fetches, repeated npm/pip downloads, RPC/API polling, logs/artifacts, redundant jobs and other avoidable transfers. Prefer event-driven, shallow, cached and change-only transfers.
3. **Disk usage** — root-filesystem utilisation/free space, runner workspaces, caches, logs, databases, temporary worktrees/artifacts and unbounded retention/growth.

Recommendations are advisory/report-only. Never reduce API or bandwidth cost by weakening wallet/signing, LIVE/ARMED, quote/simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation protections. Never automatically delete wallet material, databases or audit evidence.
