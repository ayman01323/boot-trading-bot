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

## Universal AI agent messaging

Before claiming that Copilot cannot send a message to GPT, Claude, Gemini, DeepSeek, or all agents, read `AI_AGENT_MESSAGING.md` from current `main`.

For a new cross-agent communication on branch `ai-mailbox`:

- Copilot may update **only** `.github/ai-mailbox/bus-from-copilot.md`;
- use `AI_BUS`, a unique `message_id`, `from: COPILOT`, one supported `to:` agent or `to: ALL`, `mode: DIRECT`, and `max_hops: 1`;
- read only `.github/ai-mailbox/bus-to-copilot.md` for the result and require the same `message_id`;
- for several selected agents rather than `ALL`, send one message at a time and wait for its matching reply before overwriting the sender file.

Delivery is automatic and event-driven. A valid sender-mailbox push wakes the universal relay, which automatically invokes the addressed provider; `to: ALL` invokes every other supported provider once. The recipient does **not** poll a mailbox to discover new messages. The sender reads its correlated `bus-to-copilot.md` result after the relay completes.

Example send:

```text
AI_BUS
message_id: copilot-to-claude-20260822-001
from: COPILOT
to: CLAUDE
mode: DIRECT
max_hops: 1

Claude, please review this communication-only question.
```

This is a communication-only mailbox exception. It grants no authority to edit any other `ai-mailbox` path or to deploy, trade, change LIVE/ARMED or risk/capital settings, access wallets/signing material or secrets, or use root/sudo.

## Engineering Audit: cost and VPS resource efficiency

During any Engineering Audit / full-repository bug audit, the agent must review the deterministic baseline's `operational_efficiency_audit` section and explicitly check:

- **API/model cost:** unnecessary GPT/Gemini/Claude/Copilot calls, duplicate reviews/adjudications, excessive multi-provider fan-out, oversized prompts, unnecessary CLI installs and missing material-change/cache gates.
- **Server bandwidth:** self-hosted workflow cadence, repository checkouts/fetches, npm/pip downloads, RPC/API polling, logs/artifacts and duplicate jobs. Prefer event-driven, shallow, cached and change-only transfers.
- **Disk usage:** root filesystem use/free space, runner workspaces, caches, logs, databases, worktrees/artifacts and retention/growth.

Use the sanitised `ai-reviews:engineering/ops/latest.json` snapshot when available. Host network counters are host-wide and must not be claimed as bot-only traffic without further evidence.

### Trade latency and infrastructure economics are mandatory

Every Engineering Audit must also inspect `trade_latency` and `infrastructure` in the sanitised VPS snapshot and report, for every blockchain with observed trades:

- the exact latency metric being measured and its measurement coverage;
- per-trade latency evidence where available;
- current-24h p50 and p95 latency compared with the preceding six-day same-server baseline;
- RPC round-trip latency separately from transaction inclusion/copy latency;
- seven-day trade share so recommendations prioritise chains that actually affect trading;
- any evidenced relationship between latency and execution success, slippage or realised P&L; never infer an effect that the telemetry does not support.

Treat the same server's preceding six days as the default **normal** comparison. If fewer than five historical measurements exist, report `INSUFFICIENT DATA` rather than inventing a normal latency value. Never substitute an estimated protocol block time for measured trade latency.

The infrastructure conclusion must state `KEEP`, `BENCHMARK`, or `MOVE` and explain why. Compare the current provider/region/monthly cost against any alternative provider/region/monthly cost, expected latency improvement, affected trade share and likely trading benefit. If current cost, region, candidate price, or measured benefit is unknown, say `UNKNOWN` and do not fabricate it. Any alternative price must identify a source/date or be marked unverified.

Do not recommend moving the server merely because another location has lower ping. A move requires a cost-adjusted trading case based on measured chain-weighted latency and execution evidence. Where several chains have materially different optimal regions, consider split infrastructure or chain-specific workers before recommending a wholesale migration.

All recommendations are report-only. Cost optimisation must never weaken wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce or execution-reconciliation protections. Do not automatically delete wallets, databases or audit evidence.
