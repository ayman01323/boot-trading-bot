# Strategy Factory messaging

Strategy Factory uses **one primary messaging transport** for normal agent communication: the local VPS WebSocket bus at `ws://127.0.0.1:8765`.

Supported agents are:

- GPT
- Claude
- Gemini
- DeepSeek
- Grok
- Copilot

## Claude has two explicit divisions

Claude is one agent with **two distinct operating divisions**. Operator-facing requests must identify the division; bare `claude` is ambiguous and must not be used in `/aichat` or `scripts/strategy_factory_chat.py`.

### CLAUDE GENERAL

Use `claude-general` for:

- general discussion;
- governance and organisational design;
- strategy/engineering critique that does not require repository mutation;
- research, reasoning and advisory work;
- ordinary Strategy Factory conversation.

Claude General uses the persistent Strategy Factory WebSocket recipient and its bounded SQLite conversation memory. It is still an automated provider invocation for each new message and must not claim that it is the interactive Claude Code terminal session. Messages are tagged `CLAUDE_DIVISION: GENERAL` and `CLAUDE_IDENTITY: AUTOMATED_GENERAL`.

### CLAUDE CODING

Use `claude-coding` for:

- repository/code investigation requiring Claude Code context;
- implementation, tests, branches and PR-oriented work;
- repository-aware coding review where the persistent Claude Code handoff/session is the intended recipient.

Claude Coding is routed to the existing Claude Code git mailbox/handoff path rather than to the general provider worker. A coding request written to `.github/ai-mailbox/gpt-to-claude.md` must include:

```text
division: CODING
identity_required: PERSISTENT_AGENT
```

The persistent Claude Code reply must identify itself with:

```text
division: CODING
identity: PERSISTENT_AGENT
```

A mailbox commit proves that the coding request was queued, not that Claude Coding has read it. Receipt/reply evidence must remain correlated by `message_id`/`in_reply_to`.

### Operator examples

Telegram:

```text
/aichat claude-general review this governance proposal
/aichat claude-coding inspect and fix this repository bug
```

VPS CLI:

```bash
python scripts/strategy_factory_chat.py claude-general 'review this governance proposal'
python scripts/strategy_factory_chat.py claude-coding 'inspect and fix this repository bug'
```

The two divisions must never be silently substituted for one another. If the wrong division is requested or unavailable, fail clearly rather than answering through the other division without disclosure.

The old GitHub `ai-mailbox` path remains fallback/audit for ordinary providers, but it is also the deliberate durable handoff path for **Claude Coding** because that division is repository/session oriented rather than the general provider worker.

## One transport, two routing modes

For ordinary/general Strategy Factory communication there are not two competing messaging systems.

### DIRECT mode

DIRECT is one-to-one communication over the shared WebSocket transport:

```text
GPT -> Gemini
Claude General -> GPT
Gemini -> Grok
DeepSeek -> Claude General
```

Example:

```bash
python scripts/ai_agent_ws_send.py \
  --from gpt \
  --to gemini \
  --message 'Review this question.'
```

`scripts/ai_agent_ws_send.py` is only a CLI/front end. Registration, delivery/ACK correlation, timeout behaviour and final reply handling live in the shared `scripts/strategy_factory_transport.py` client.

### COUNCIL mode

COUNCIL is one-to-many-to-one orchestration over that **same** shared transport:

```text
MASTER request
   -> Cost Router
      -> selected adviser(s) over Strategy Factory WebSocket
         -> adviser replies
            -> GPT final adjudication
```

The Council is a governance/orchestration layer, not a second transport. `learnerbot/strategy_factory_council_transport_patch.py` adapts Council adviser requests to the shared Strategy Factory client.

Unless a council task explicitly requires the coding division, the Claude adviser in normal architecture/governance discussion is **Claude General**. Repository implementation remains under the normal coding/handoff/change-control path.

The Cost Router still decides which advisers are required. Critical trading, security and deployment changes still use the full Council. GPT remains final adjudicator for repository changes. Existing protected policy/deployment gates remain unchanged.

## Canonical user-to-agent chat identity

The user-facing canonical identity is `MASTER`. `MASTER` is a sender/client identity only; it is not a seventh AI worker, cannot be targeted as an AI recipient, and is never included in Council fan-out.

Use Telegram:

```text
/aichat gemini what did GPT ask you?
/aichat claude-general review this idea
/aichat claude-coding inspect this code defect
/aichat grok summarise the latest Strategy Factory context you have
```

or the VPS CLI:

```bash
python scripts/strategy_factory_chat.py gemini 'what did GPT ask you?'
```

General-agent paths send `MASTER -> agent` through the same persistent Strategy Factory worker and store the turn in the same durable conversation history used by agent-to-agent messages. Claude Coding instead uses the durable Claude Code handoff/mailbox because it is a different operating division.

A separate vendor browser conversation such as Gemini Web, Claude Web, Grok Web or another third-party chat is an **external/unlinked session** unless it is explicitly bridged into Strategy Factory. Do not describe an external browser tab as either Claude division unless it has been explicitly connected to the relevant routing path.

## Delivery evidence

For normal communication, use:

```text
DELIVERED -> ACKNOWLEDGED -> REPLIED
```

For deterministic bounded tasks, use:

```text
DELIVERED -> ACKNOWLEDGED -> ACCEPTED -> EXECUTING -> COMPLETED
```

For Claude Coding mailbox routing, use:

```text
QUEUED -> PERSISTENT_AGENT_REPLIED
```

and require the coding division/identity headers. Do not claim an agent received a message merely because a Git mailbox commit exists.

## Automatic recipient awareness and persistence

Persistent WebSocket workers stay connected to the loopback bus. A normal recipient **must not poll GitHub**, SQLite, or a mailbox; messages are pushed automatically to the connected worker, and queued messages are delivered when that worker reconnects.

Claude Coding is the deliberate exception because it is the persistent repository/Claude Code handoff division. Its git mailbox is a durable task handoff, not the general WebSocket provider notification path.

SQLite at `/var/tmp/boot/ai_agent_bus.sqlite3` is the durable queue, audit record and bounded Strategy Factory conversation-memory source. It is not the notification transport.

The runtime status file is `/var/tmp/boot/ai_agent_ws_status.json`.

## Cost policy

Routing, queueing, validation, delivery and ACKs use **zero AI/model tokens**. Bounded deterministic `ws-bus-v2` tasks also use zero model calls.

DIRECT should be the default for routine communication. COUNCIL should be used only when the Cost Router or governance policy requires multiple advisers. Successful adviser replies may be cached/reused on retry to avoid duplicate spend.

Default low-cost worker models are configured separately from the transport and may be overridden when stronger reasoning is materially required.

## Safe deterministic tasks

The shared bus also carries allow-listed `ws-bus-v2` tasks such as:

- `READ_FILE`
- `LIST_FILES`
- `SEARCH_CODE`
- `RUN_TESTS`
- `PY_COMPILE`
- `GIT_STATUS`
- `GIT_DIFF`

The executor does not accept arbitrary shell commands. Repository mutation, deployment/restart, trading, LIVE/ARMED changes, risk/capital changes, wallet/signing operations and secrets remain protected and cannot be authorised merely by sending a bus task.

## GitHub mailbox fallback

For normal agents the GitHub mailbox is not a second normal messaging system; it is fallback/audit only. For Claude Coding it is also the deliberate repository-session handoff channel described above.

Do not claim a Git mailbox commit itself proves recipient receipt. A mailbox commit proves only that a handoff was written to Git; correlated delivery/reply evidence is still required.

## Runtime and protected deployment

Production normally runs the embedded Strategy Factory bus inside `learnerbot.service` with six persistent general workers. Claude Coding remains a separate coding division rather than a seventh council member.

The optional `scripts/install_ai_agent_ws_bus.sh` installer remains available for a deliberately separate standalone/systemd deployment and installs the same shared transport client used by DIRECT mode.

Production deployment remains outside the messaging transport. It uses the restricted wrapper `/usr/local/sbin/deploy-boot-trading-bot`, which runs the repository test gate and restarts the service only after that gate passes. DIRECT, COUNCIL or Claude-division routing does not itself grant deployment authority.

Protocol: `ws-bus-v2`.
