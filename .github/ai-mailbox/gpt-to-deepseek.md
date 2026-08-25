GPT_TO_DEEPSEEK
message_id: 2026-08-25T20-47-deepseek-finish-sibot1-engine
source_sha: b96d2ea09f1c3d53bd6f8a5a840549b5e651549b
status: REQUEST
constraints: finish as a bounded draft/patch proposal only; no merge, deploy, restart, real-money trading, LIVE/ARMED/AUTO changes, capital/risk changes, wallet/signing/private-key access, secrets, or arbitrary sudo. Preserve central PoolCheck and fail-closed semantics.

The user wants you to finish your SiBot 1 trading engine. Current SiBot 1 has independent deterministic engines gpt/base, gemini/solana, and grok/solana using the shared MarketEvent -> TradeIntent/ExitIntent contract, central PoolCheck, virtual capital, paper accounting, and separate protected execution boundary. Your engine must fit that architecture and remain SHADOW/PAPER until independently reviewed.

Please complete the DeepSeek engine work now as follows:
1. If you previously designed or implemented a DeepSeek-specific engine, state exactly what remains unfinished and provide the completed file-level patch/diff.
2. If no prior DeepSeek engine implementation is available to you, design and provide a complete fourth-engine implementation that is materially distinct from GPT net-edge arbitrage, Gemini PulseFlow, and Grok CompactFlow. Base it only on evidence fields the current bot can obtain cheaply/reliably; do not invent unavailable data.
3. Provide engine.py, strategy.py, settings_schema.py/settings example, registry/runtime integration changes, tests, health counters, and scoreboard/audit expectations.
4. Include explicit entry and exit rules, stale-data rejection, minimum evidence, PoolCheck handoff, position ownership and emergency-exit behaviour.
5. Keep the engine unable to sign/broadcast and unable to bypass central PoolCheck. No direct private-key/API-secret access.
6. Define acceptance tests and expected signal frequency/cost. Prefer cached/RPC/WebSocket/local data and avoid paid calls unless justified.
7. Return the implementation as an actionable bounded patch or exact complete file contents for GPT to review/integrate. Do not push main yourself.

Target: a production-quality SHADOW engine ready for independent review and tests, not merely a high-level idea.
