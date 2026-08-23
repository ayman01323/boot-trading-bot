# Strategy Factory messaging

Strategy Factory uses **one primary messaging transport** for all agent communication: the local VPS WebSocket bus at `ws://127.0.0.1:8765`.

Supported agents are:

- GPT
- Claude
- Gemini
- DeepSeek
- Grok
- Kimi
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
GPT -> Kimi
```

Example:

```bash
python scripts/ai_agent_ws_send.py \
  --from gpt \
  --to kimi \
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

The Cost Router still decides which advisers are required. The staged Kimi integration does **not** yet make Kimi mandatory in protected MASTER-change adviser quorum or scheduled review completeness. That promotion is deliberately deferred until a real Kimi credential passes an end-to-end live diagnostic. GPT remains final adjudicator for repository changes and existing protected policy/deployment gates remain unchanged.

## Subject threads for parallel work

Strategy Factory messages may carry both a human-readable `subject` and a stable `thread_id`. The same normalised subject deterministically maps to the same thread, so several agents can collaborate on one topic while unrelated topics remain isolated.

Example DIRECT messages:

```bash
python scripts/ai_agent_ws_send.py \
  --from gpt \
  --to kimi \
  --subject 'HOOD fraud' \
  --message 'Review the pool manipulation evidence.'

python scripts/ai_agent_ws_send.py \
  --from gpt \
  --to claude \
  --subject 'HOOD fraud' \
  --message 'Challenge Kimi’s conclusion.'
```

Both messages use the same subject thread even though the recipient is different. A separate subject such as `Server latency` receives a different thread and cannot enter the HOOD thread’s bounded memory.

Thread behaviour:

- `subject` is human-readable and limited to 160 characters.
- `thread_id` is stored on every threaded message and reply.
- Supplying a subject without an explicit thread id generates a stable thread id from the subject.
- Supplying `--thread-id` allows an exact existing thread to be continued.
- Replies, ACK/status events and durable SQLite records retain the thread metadata.
- Thread memory is bounded exactly like existing memory, but reads **only that thread**.
- Thread memory is shared across Strategy Factory agents participating in that subject, allowing GPT, Claude, Gemini, DeepSeek, Grok, Kimi and Copilot to work from the same bounded topic history.
- Legacy messages that omit both fields continue to use the older unthreaded per-agent memory behaviour.

Telegram MASTER syntax uses `[subject]` immediately after the agent name:

```text
/aichat kimi [HOOD fraud] review the latest finding
/aichat claude [HOOD fraud] challenge Kimi's conclusion
/aichat grok [Server latency] compare p95 execution latency
```

This lets the Strategy Factory run several subjects in parallel without context contamination.

## Canonical user-to-agent chat identity

The user-facing canonical identity is `MASTER`. `MASTER` is a sender/client identity only; it is not an eighth AI worker, cannot be targeted as an AI recipient, and is never included in Council fan-out.

Use Telegram:

```text
/aichat kimi what did GPT ask you?
/aichat claude review this idea
/aichat grok summarise the latest Strategy Factory context you have
```

or the VPS CLI:

```bash
python scripts/strategy_factory_chat.py kimi 'what did GPT ask you?'
python scripts/strategy_factory_chat.py kimi 'review the latest finding' --subject 'HOOD fraud'
```

Both paths send `MASTER -> agent` through the same persistent Strategy Factory worker and store the turn in the same durable conversation history used by agent-to-agent messages. Threaded messages recall only their subject thread; legacy unthreaded messages retain the existing bounded per-agent memory behaviour.

A separate vendor browser conversation such as Gemini Web, Claude Web, Grok Web, Kimi Web or another third-party chat is an **external/unlinked session** unless it is explicitly bridged into Strategy Factory. Do not describe an external browser tab as the Strategy Factory agent and do not expect it to know Strategy Factory messages automatically. The canonical interactive agent is the persistent Strategy Factory worker reached through `/aichat` or `scripts/strategy_factory_chat.py`.

## Kimi provider configuration

Kimi uses the OpenAI-compatible Moonshot/Kimi API rather than browser automation. The default provider configuration is:

```text
model: kimi-k2.6
base URL: https://api.moonshot.ai/v1
credential: KIMI_API_KEY (preferred) or MOONSHOT_API_KEY
```

`KIMI_COUNCIL_MODEL` can override the model without changing code. Routine worker traffic uses K2.6 with thinking disabled by default to limit latency and cost; `KIMI_THINKING=enabled` opts in where deeper reasoning is justified. A stronger Kimi model can be selected through configuration after its exact API model ID and cost policy are validated.

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

SQLite at `/var/tmp/boot/ai_agent_bus.sqlite3` is the durable queue, audit record and bounded Strategy Factory conversation-memory source. It is not the notification transport. Subject threads add `thread_id` and `subject` fields to this durable record using an additive migration that preserves existing messages.

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

Production normally runs the embedded Strategy Factory bus inside `learnerbot.service` with seven persistent workers after Kimi activation. The optional `scripts/install_ai_agent_ws_bus.sh` installer remains available for a deliberately separate standalone/systemd deployment and installs the same shared transport client used by DIRECT mode.

Production deployment remains outside the messaging transport. It uses the restricted wrapper `/usr/local/sbin/deploy-boot-trading-bot`, which runs the repository test gate and restarts the service only after that gate passes. DIRECT or COUNCIL messaging does not itself grant deployment authority.

Protocol: `ws-bus-v2`.
