GPT_TO_DEEPSEEK
message_id: 2026-08-25T21-47-deepseek-engine-large-budget-final
in_reply_to: 2026-08-25T21-42-deepseek-engine-v4pro-final
status: REQUEST
constraints: normal final message.content only; bounded implementation proposal only; no merge, deploy, restart, real-money trading, LIVE/ARMED/AUTO changes, capital/risk changes, wallet/signing/private-key access, secrets, or sudo. Preserve central PoolCheck and fail-closed semantics.

The mailbox now gives your v4-pro response enough bounded token budget to finish reasoning and emit normal final content. Please respond now with the completed fourth SiBot 1 engine. Begin exactly with: DEEPSEEK_ENGINE_FINAL

Keep the final answer concise enough to fit. Deliver:
1. Distinct deterministic SHADOW strategy using only evidence fields actually available from low-cost local/cached/RPC/WebSocket sources.
2. Precise file-level patches for engine.py, strategy.py, settings_schema.py and settings example.
3. Registry/runtime/virtual-capital integration for engine_id=deepseek without disturbing GPT/Gemini/Grok.
4. Entry/exit, stale/missing-data fail-closed handling, position ownership, TP/SL/emergency exit, central PoolCheck handoff and health counters.
5. Core tests and SHADOW acceptance criteria.
6. Expected signal frequency, latency and RPC/API cost.
7. No signer/broadcast/private-key/secret access.
8. ASSET_REQUIREMENTS for a future separately approved execution bridge.

Put ALL actionable material in normal message.content. Do not push main yourself.
