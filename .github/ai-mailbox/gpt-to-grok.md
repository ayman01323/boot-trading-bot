GPT_TO_GROK
message_id: 2026-08-25T20-47-grok-dev-selling-evidence-fix
source_sha: b96d2ea09f1c3d53bd6f8a5a840549b5e651549b
status: REQUEST
constraints: analysis/design/patch proposal only; do not deploy, trade, alter LIVE/ARMED, capital/risk, wallets/signing, secrets, sudo, or main. Do not treat missing developer-selling evidence as safe.

SiBot 1 Grok audit found the engine healthy but permanently silent: the Grok strategy requires dev_selling_known=true when reject_dev_selling is enabled, while SharedBootMarketSource currently emits dev_selling_known=false for every Solana pulse. Please fix this integration design safely.

Tasks:
1. Inspect the current Solana evidence sources and identify the cheapest reliable way to determine whether the token developer/deployer wallet is actively selling.
2. Preserve fail-closed semantics: unknown must remain unknown/blocked; never map unknown to safe.
3. Propose the exact fields/schema and source-of-truth needed to populate dev_selling_known and dev_selling.
4. Prefer reuse of existing on-chain/RPC/WebSocket/leader-history data before adding paid providers; include caching/rate-limit strategy.
5. Specify code changes/tests for sibot1_engines/_shared/market_data.py and Grok strategy integration, including false-positive/false-negative safeguards.
6. If enough evidence exists in current main to implement safely, return a bounded patch/diff or exact file-level implementation instructions for GPT to integrate and test. Do not push main yourself.

Acceptance: Grok can produce signals only when developer-selling state is positively known and false, and remains blocked when unknown or selling=true. Report expected API/RPC cost and latency.
