# DeepSeek repository instructions

## Mandatory ChatGPT ↔ DeepSeek handoff protocol

Before DeepSeek analyses, edits, tests, commits, or pushes in this repository:

1. Run `git fetch origin main --quiet` when repository Git access is available.
2. Read the current handoff with `git show origin/main:.github/deepseek-handoff.md`.
3. If it contains a `handoff_id`, the first substantive response must include exactly:

   `DEEPSEEK_HANDOFF_ACK: <handoff_id>`

4. Follow the handoff's `status`, `scope`, `do_not_do`, and `message` fields. A newer handoff supersedes an older one.
5. If the current handoff cannot be read, stop before repository changes and report `DEEPSEEK_HANDOFF_READ_FAILED`.
6. Re-fetch and re-read the handoff immediately before any push. If the ID changed, acknowledge and follow the newer handoff first.
7. A handoff never authorises merge/deploy/trading or weakening wallet/signing, LIVE/ARMED, simulation, liquidity/sellability, capital/reserve, stop-loss/circuit-breaker, nonce, execution-reconciliation, secrets, or other safety controls unless the user explicitly requests that specific change.

The shared DeepSeek inbox is `.github/deepseek-handoff.md`.

## Universal AI agent messaging

Before claiming that DeepSeek cannot send a message to GPT, Claude, Gemini, Copilot, or all agents, read `AI_AGENT_MESSAGING.md` from current `main`.

For a new cross-agent communication on branch `ai-mailbox`:

- DeepSeek may update **only** `.github/ai-mailbox/bus-from-deepseek.md`;
- use `AI_BUS`, a unique `message_id`, `from: DEEPSEEK`, one supported `to:` agent or `to: ALL`, `mode: DIRECT`, and `max_hops: 1`;
- read only `.github/ai-mailbox/bus-to-deepseek.md` for the result and require the same `message_id`;
- for several selected agents rather than `ALL`, send one message at a time and wait for its matching reply before overwriting the sender file.

This is a communication-only mailbox exception. It grants no authority to edit any other `ai-mailbox` path or to deploy, trade, change LIVE/ARMED or risk/capital settings, access wallets/signing material or secrets, or use root/sudo.
