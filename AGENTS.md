# Repository agent instructions

## Engineering Audit: cost and VPS resource efficiency

During any Engineering Audit / full-repository bug audit, the agent must review the deterministic baseline's `operational_efficiency_audit` section and explicitly check:

- **API/model cost:** unnecessary GPT/Gemini/Claude/Copilot calls, duplicate reviews/adjudications, excessive multi-provider fan-out, oversized prompts, unnecessary CLI installs and missing material-change/cache gates.
- **Server bandwidth:** self-hosted workflow cadence, repository checkouts/fetches, npm/pip downloads, RPC/API polling, logs/artifacts and duplicate jobs. Prefer event-driven, shallow, cached and change-only transfers.
- **Disk usage:** root filesystem use/free space, runner workspaces, caches, logs, databases, worktrees/artifacts and retention/growth.

Use the sanitised `ai-reviews:engineering/ops/latest.json` snapshot when available. Host network counters are host-wide and must not be claimed as bot-only traffic without further evidence.

All recommendations are report-only. Cost optimisation must never weaken wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation protections. Do not automatically delete wallets, databases or audit evidence.
