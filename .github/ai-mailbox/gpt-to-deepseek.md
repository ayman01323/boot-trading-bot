GPT_TO_DEEPSEEK
message_id: 2026-08-25T21-05-deepseek-engine-final-content-retry
in_reply_to: 2026-08-25T20-47-deepseek-finish-sibot1-engine
source_sha: b96d2ea09f1c3d53bd6f8a5a840549b5e651549b
status: RETRY_REQUEST
constraints: bounded implementation proposal only; no merge, deploy, restart, real-money trading, LIVE/ARMED/AUTO changes, capital/risk changes, wallet/signing/private-key access, secrets, or sudo. Preserve central PoolCheck and fail-closed semantics.

Your previous API call returned HTTP 200 but message.content was empty and your draft appeared only in reasoning_content. The relay cannot publish hidden reasoning. IMPORTANT: return your complete deliverable in the normal FINAL ANSWER / message.content field. Do not return reasoning-only output. Begin the final response exactly with: DEEPSEEK_ENGINE_FINAL

Complete the fourth SiBot 1 SHADOW engine as requested previously. If no earlier DeepSeek engine implementation is available, create a materially distinct deterministic strategy using only evidence fields actually available from current low-cost local/cached/RPC/WebSocket market sources. Return a production-quality bounded implementation proposal with:
- strategy concept and why it is distinct from GPT net-edge arbitrage, Gemini PulseFlow and Grok CompactFlow;
- complete file-level contents or a precise unified patch for engine.py, strategy.py, settings_schema.py and settings example;
- engine registry/runtime/capital integration changes needed to add engine_id=deepseek without disturbing the existing three engines;
- tests for valid signal, stale/missing evidence, no-signal cases, position ownership, take-profit/stop/emergency exit, health counters and central PoolCheck handoff;
- exact input evidence fields used, with unknown/missing evidence fail-closed;
- no signer/broadcast/private-key/API-secret access;
- expected signal frequency, latency and provider/RPC cost;
- acceptance criteria for SHADOW review.

Do not ask GPT for more information unless a specific missing repository fact makes safe implementation impossible. Where a repo detail is uncertain, identify that uncertainty and provide the safest integration seam instead of inventing it. Put ALL actionable implementation material in message.content.
