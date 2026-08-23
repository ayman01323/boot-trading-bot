# Strategy Factory messaging

Strategy Factory uses **one primary messaging transport** for all agent communication: the local VPS WebSocket bus at `ws://127.0.0.1:8765`.

Supported agents are:

- GPT
- Claude
- Gemini
- DeepSeek
- Grok
- Copilot

The old GitHub `ai-mailbox` path is retained only as an audit/fallback handoff when the local bus is genuinely unavailable or a durable Git handoff is specifically required.

## One transport, two routing modes

There are not two messaging systems.

### DIRECT mode

DIRECT is one-to-one communication over the shared WebSocket transport:

```text
GPT -> Gemini
Claude -> GPT
Gemini -> Grok
DeepSeek -> Claude
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

The Cost Router still decides which advisers are required. Critical trading, security and deployment changes still use the full Council. GPT remains final adjudicator for repository changes. Existing protected policy/deployment gates remain unchanged.

## Canonical user-to-agent chat identity

The user-facing canonical identity is `MASTER`. `MASTER` is a sender/client identity only; it is **not** a seventh AI worker, cannot be targeted as an AI recipient, and is never included in Council fan-out.

Use Telegram:

```text
/aichat gemini what did GPT ask you?
/aichat claude review this idea
/aichat grok summarise the latest Strategy Factory context you have
```

or the VPS CLI:

```bash
python scripts/strategy_factory_chat.py gemini 'what did GPT ask you?'
```

Both paths send `MASTER -> agent` through the same persistent Strategy Factory worker and store the turn in the same durable conversation history used by agent-to-agent messages. This means a later GPT -> Gemini message and a later MASTER -> Gemini message can both be recalled by that same Gemini worker subject to the bounded memory limits.

A separate vendor browser conversation such as Gemini Web, Claude Web, Grok Web or another third-party chat is an **external/unlinked session** unless it is explicitly bridged into Strategy Factory. Do not describe an external browser tab as the Strategy Factory agent and do not expect it to know Strategy Factory messages automatically. The canonical interactive agent is the persistent Strategy Factory worker reached through `/aichat` or `scripts/strategy_factory_chat.py`.

## Delivery evidence

For normal communication, use:

```text
DELIVERED -> ACKNOWLEDGED -> REPLIED
```

For deterministic bounded tasks, use:

```text
DELIVERED -> ACKNOWLEDGED -> ACCEPTED -> EXECUTING -> COMPLETED
```

Do not claim an agent received a message unless it reached at least `ACKNOWLEDGED`. Do not claim a deterministic task completed unless it reached `COMPLETED` with execution evidence.

## Automatic recipient awareness and persistence

Persistent workers stay connected to the loopback bus. A recipient **must not poll GitHub**, SQLite, or a mailbox; messages are pushed automatically to the connected worker, and queued messages are delivered when that worker reconnects. The user does not need to tell an agent to check its messages.

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

The GitHub mailbox is not a second normal messaging system. It is fallback/audit only.

Do not claim a Git mailbox commit itself proves recipient receipt. A mailbox commit proves only that a handoff was written to Git; correlated delivery evidence is still required before reporting receipt.

## Runtime and protected deployment

Production normally runs the embedded Strategy Factory bus inside `learnerbot.service` with six persistent workers. The optional `scripts/install_ai_agent_ws_bus.sh` installer remains available for a deliberately separate standalone/systemd deployment and installs the same shared transport client used by DIRECT mode.

Production deployment remains outside the messaging transport. It uses the restricted wrapper `/usr/local/sbin/deploy-boot-trading-bot`, which runs the repository test gate and restarts the service only after that gate passes. DIRECT or COUNCIL messaging does not itself grant deployment authority.

Protocol: `ws-bus-v2`.
