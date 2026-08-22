# Universal AI agent messaging

This repository has one primary low-cost, event-driven transport for communication between GPT, Claude, Gemini, DeepSeek and Copilot: the **local VPS WebSocket bus**. The older GitHub `ai-mailbox` transport remains only as an audit/fallback path.

## Primary rule: local WebSocket bus

For a new routine cross-agent communication, use the local bus first. Do not create a Git commit, GitHub Action run, or provider fan-out merely to transport one message.

The broker listens on loopback by default at `ws://127.0.0.1:8765`. Persistent recipient workers stay connected for GPT, Claude, Gemini, DeepSeek and Copilot. Routing itself is deterministic and uses **zero AI/model tokens**.

Send one direct message with:

```bash
python scripts/ai_agent_ws_send.py \
  --from gemini \
  --to gpt \
  --message 'Please review this communication-only question.'
```

Replace the sender, recipient and body as required. Supported agents are `gpt`, `claude`, `gemini`, `deepseek`, and `copilot`.

## Automatic recipient awareness

Automatic awareness is mandatory. A recipient must not poll GitHub, SQLite, or a mailbox and the user must not have to say “check your messages”.

The live sequence is:

```text
sender -> WebSocket broker -> connected recipient worker -> ACK -> provider -> reply -> sender
```

SQLite is only the durable queue/audit record. It is not the notification mechanism. If a worker is temporarily offline, the message remains queued and is pushed automatically when that worker reconnects.

Delivery states are meaningful:

- `QUEUED`: stored but recipient worker is not currently connected;
- `DELIVERED`: pushed to a recipient socket;
- `ACKNOWLEDGED`: recipient worker received it and ACKed before inference;
- `REPLIED`: the addressed provider returned a reply.

Do not tell the user an agent “received” a message unless the message reached at least `ACKNOWLEDGED`.

## Cost policy

The bus must optimise total cost, not merely token price:

1. routing, validation, queueing and delivery use no model;
2. direct one-recipient messages are the default;
3. broadcast fan-out is disabled by default and requires `AI_AGENT_BUS_ALLOW_ALL=1`;
4. recipient workers use low-cost models for routine coordination unless explicitly overridden;
5. prompts are deliberately short and replies are instructed to stay concise;
6. no GitHub Action, checkout, package install, or Git fetch is required per message.

Default routine worker models:

- GPT: `gpt-5-nano`;
- Gemini: `gemini-3.1-flash-lite`;
- Claude: `claude-haiku-4-5`;
- DeepSeek: `deepseek-v4-flash`;
- Copilot: existing bounded Copilot CLI/provider path using a persistent one-time CLI installation at `/var/tmp/boot-copilot-cli/bin/copilot`.

Override a worker model only when needed with `AI_BUS_GPT_MODEL`, `AI_BUS_GEMINI_MODEL`, `AI_BUS_CLAUDE_MODEL`, or `AI_BUS_DEEPSEEK_MODEL`.

Escalate to a stronger model only when the substance of the task requires materially better reasoning. Do not use a frontier model merely to acknowledge receipt, route a message, ask for status, or pass a short coordination note.

## Safety boundary

This transport is communication-only. A bus message or reply does **not** authorise repository edits, merge/deploy/restart actions, trading, LIVE/ARMED changes, risk/capital changes, wallet/signing access, secrets, or arbitrary sudo. Those still require the normal trusted workflow and user authority.

Never put API keys, tokens, private keys, mnemonics, seed phrases, wallet credentials, or other secrets in message bodies.

The broker binds to loopback by default. If it is ever bound beyond loopback, `AI_AGENT_BUS_TOKEN` is mandatory. Do not expose an unauthenticated agent bus publicly.

## GitHub mailbox fallback

The `ai-mailbox` branch remains available only when the local WebSocket bus is genuinely unavailable or when a durable Git handoff is specifically useful.

Fallback sender files remain:

- GPT: `.github/ai-mailbox/bus-from-gpt.md`
- Claude: `.github/ai-mailbox/bus-from-claude.md`
- Gemini: `.github/ai-mailbox/bus-from-gemini.md`
- DeepSeek: `.github/ai-mailbox/bus-from-deepseek.md`
- Copilot: `.github/ai-mailbox/bus-from-copilot.md`

Fallback replies remain in the matching `.github/ai-mailbox/bus-to-<sender>.md` file. Continue to require exact `message_id` correlation.

Do not claim a Git mailbox commit itself proves recipient receipt. It proves only that the message was written to Git. Recipient receipt requires a correlated relay result.

## Runtime and deployment behaviour

The production path does **not** require new sudo permissions or a separate root-owned daemon. The existing `learnerbot.service` already runs `python -m learnerbot run`; that runtime imports `learnerbot.ai_agent_ws_runtime_patch`, which starts the loopback broker and all five persistent recipient workers in a daemon sidecar thread.

The embedded runtime:

- starts only for the real `learnerbot run` command, not short administrative/test commands;
- binds `127.0.0.1:8765` only;
- stores the durable queue in `/var/tmp/boot/ai_agent_bus.sqlite3` using SQLite WAL mode;
- writes non-secret runtime state to `/var/tmp/boot/ai_agent_ws_status.json`;
- reconnects recipient workers automatically;
- skips itself if another broker is already serving that loopback port;
- can be disabled explicitly with `AI_AGENT_WS_AUTOSTART=0`.

Deployment uses the repository's existing restricted root wrapper, `/usr/local/sbin/deploy-boot-trading-bot`, which already verifies the exact current `origin/main`, installs declared Python dependencies, runs compile/tests, and restarts `learnerbot` only after the gate passes. No broad runner sudo permission is added for the WebSocket bus.

The protected WebSocket deployment also installs GitHub Copilot CLI once, without sudo, under `/var/tmp/boot-copilot-cli` if it is missing. The worker only prepends that persistent directory to `PATH` for the duration of a Copilot message, so there is no repeated installation cost and no effect on unrelated learnerbot subprocesses.

`scripts/install_ai_agent_ws_bus.sh` remains an optional one-time standalone/systemd installer for an administrator who deliberately wants a separate broker service. It is **not** the automatic production deployment path and the GitHub runner is not granted arbitrary sudo to run it.

Protocol revision: `ws-bus-v1`.
