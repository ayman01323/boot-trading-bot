# Repository agent instructions

## Mandatory Copilot handoff protocol

When the acting agent is GitHub Copilot / Copilot coding agent, before analysis, edits, tests, commits, or PR creation:

1. Fetch current `origin/main` when available and read `.github/copilot-handoff.md` from current main. Do not rely only on a stale feature-branch copy.
2. If the file contains a `handoff_id`, acknowledge it exactly as:

   `COPILOT_HANDOFF_ACK: <handoff_id>`

   Put the acknowledgement in the first substantive issue/PR response; when Copilot creates a PR, also include it in the PR description or the first human-readable report/comment.
3. Follow the handoff's `status`, `scope`, `do_not_do`, and `message` fields. A newer handoff supersedes an older one.
4. If the current handoff cannot be read, do not make repository changes. Report `COPILOT_HANDOFF_READ_FAILED`.
5. Re-read the handoff immediately before pushing/creating the PR. If the handoff ID changed, acknowledge and follow the newer one first.
6. The handoff does not itself authorise merge/deploy/trading or weakening wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce, execution-reconciliation, secrets, or other safety controls.

The Copilot inbox is `.github/copilot-handoff.md`.

## Engineering Audit: cost and VPS resource efficiency

During any Engineering Audit / full-repository bug audit, the agent must review the deterministic baseline's `operational_efficiency_audit` section and explicitly check:

- **API/model cost:** unnecessary GPT/Gemini/Claude/Copilot calls, duplicate reviews/adjudications, excessive multi-provider fan-out, oversized prompts, unnecessary CLI installs and missing material-change/cache gates.
- **Server bandwidth:** self-hosted workflow cadence, repository checkouts/fetches, npm/pip downloads, RPC/API polling, logs/artifacts and duplicate jobs. Prefer event-driven, shallow, cached and change-only transfers.
- **Disk usage:** root filesystem use/free space, runner workspaces, caches, logs, databases, worktrees/artifacts and retention/growth.

Use the sanitised `ai-reviews:engineering/ops/latest.json` snapshot when available. Host network counters are host-wide and must not be claimed as bot-only traffic without further evidence.

All recommendations are report-only. Cost optimisation must never weaken wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation protections. Do not automatically delete wallets, databases or audit evidence.
