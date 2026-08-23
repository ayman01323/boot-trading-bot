# Strategy Factory persistent conversation memory

Strategy Factory recipient workers use the existing WebSocket audit database as bounded conversation memory.

## What is remembered

For each agent, the worker retrieves recent successful normal Strategy Factory conversations involving that agent before making the next provider call. This allows follow-up questions such as `What did GPT ask you last time?`, `Continue your previous answer.`, and `What did you tell Claude about this?`.

The memory survives learnerbot/service restarts because the source is the durable SQLite WebSocket audit database.

## Cost controls

Memory retrieval is local SQLite only and makes zero additional AI/provider calls.

Defaults:
- `AI_BUS_MEMORY_ENABLED=1`
- `AI_BUS_MEMORY_MAX_EXCHANGES=6`
- `AI_BUS_MEMORY_MAX_CHARS=3200`

Hard implementation caps prevent more than 12 exchanges or 8,000 memory characters being injected into one provider prompt. Deterministic `ws-bus-v2` task results are not included in conversational memory.

## Scope boundary

This memory contains conversations that passed through Strategy Factory. It does not automatically read unrelated external product chats, such as a separate Gemini website conversation, Claude website conversation, or another ChatGPT conversation. If an external conversation must become Strategy Factory memory, its relevant message must first be explicitly bridged into Strategy Factory.

## Storage

No second memory database is introduced. The source of truth remains the existing WebSocket SQLite audit database, normally `/var/tmp/boot/ai_agent_bus.sqlite3`. The standalone installer passes the same `AI_AGENT_BUS_DB` path to the broker and recipient workers.
