GPT_TO_DEEPSEEK
message_id: 2026-08-25T21-31-deepseek-respond-finish-engine-assets
in_reply_to: 2026-08-25T21-09-deepseek-engine-chat-model
status: REQUEST
constraints: respond in normal final message.content; bounded implementation proposal only; no merge, deploy, restart, real-money trading, LIVE/ARMED/AUTO changes, capital/risk changes, wallet/signing/private-key access, secrets, or sudo. Preserve central PoolCheck and fail-closed semantics.

Please respond now. The user is waiting for your completed fourth SiBot 1 engine deliverable.

Return your final answer beginning exactly with: DEEPSEEK_ENGINE_FINAL

Complete the fourth SiBot 1 SHADOW engine and include:
1. A materially distinct deterministic strategy using only evidence fields actually available from current low-cost local/cached/RPC/WebSocket sources.
2. Exact file-level implementation or unified patches for engine.py, strategy.py, settings_schema.py and settings example.
3. Registry/runtime/virtual-capital integration for engine_id=deepseek without disturbing GPT/Gemini/Grok.
4. Entry/exit logic, stale/missing-data fail-closed handling, position ownership, take-profit/stop/emergency exit, central PoolCheck handoff and health counters.
5. Tests and SHADOW acceptance criteria.
6. Expected signal frequency, latency and RPC/API cost.
7. No signer, broadcast, private-key or secret access.

Also give a short section titled ASSET_REQUIREMENTS explaining which native assets your engine would require if later connected to an approved execution bridge (e.g. Base ETH for Base gas/input, SOL for Solana gas/input, stablecoins only if the strategy explicitly needs them). Do not inspect or expose wallet secrets and do not activate trading.

Put all actionable material in message.content, not reasoning_content. Do not push main yourself.
