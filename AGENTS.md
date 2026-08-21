# Repository agent instructions

## Mandatory AI identity in GitHub-visible output

Follow `docs/AI_AGENT_IDENTITY.md`. When the actual provider is GPT/Codex, every AI-authored GitHub comment, issue body, PR body and human-readable report must begin with:

`🟢 AGENT: GPT`

AI-created PR titles must start with `[GPT]`. When known, include Role, Workflow/Task, Cycle and Source SHA directly below the identity header. AI-originated commit messages should include the trailer `AI-Agent: GPT`. Do not impersonate another provider; if this instruction file is read by a different provider, use that provider's repository-specific identity rule instead.

## Engineering Audit: cost and VPS resource efficiency

During any Engineering Audit / full-repository bug audit, the agent must review the deterministic baseline's `operational_efficiency_audit` section and explicitly check:

- **API/model cost:** unnecessary GPT/Gemini/Claude/Copilot calls, duplicate reviews/adjudications, excessive multi-provider fan-out, oversized prompts, unnecessary CLI installs and missing material-change/cache gates.
- **Server bandwidth:** self-hosted workflow cadence, repository checkouts/fetches, npm/pip downloads, RPC/API polling, logs/artifacts and duplicate jobs. Prefer event-driven, shallow, cached and change-only transfers.
- **Disk usage:** root filesystem use/free space, runner workspaces, caches, logs, databases, worktrees/artifacts and retention/growth.

Use the sanitised `ai-reviews:engineering/ops/latest.json` snapshot when available. Host network counters are host-wide and must not be claimed as bot-only traffic without further evidence.

All recommendations are report-only. Cost optimisation must never weaken wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation protections. Do not automatically delete wallets, databases or audit evidence.
