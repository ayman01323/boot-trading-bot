GPT_TO_GROK
message_id: 2026-08-27T21-35-grok-known-assets-full-audit
status: REQUEST
priority: P0
subject: Audit current Grok known-assets PAPER bot

Please perform an independent REVIEW-ONLY audit of the current isolated known-assets bot. You previously said reviewing/correcting another engineer's implementation is acceptable. Do not author wallet/signer/broadcast/live-order code; instead identify defects, missing controls, data-quality gaps, test gaps, and precise corrections.

Repository: https://github.com/ayman01323/boot-trading-bot
Audit commit on main: 21b4e66741aef6abaf1baa4de8e3d28c287a8f21
Bot root: testingbots/grok_known_assets_bot/
Deployment target: Google self-hosted runner only; bot remains PAPER/SHADOW.

Inspect these current files if repository access is available:
- testingbots/grok_known_assets_bot/src/grok_known_assets_bot/core.py
- testingbots/grok_known_assets_bot/src/grok_known_assets_bot/cli.py
- testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_settings.py
- testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_strategy.py
- testingbots/grok_known_assets_bot/src/grok_known_assets_bot/research_adapter.py
- testingbots/grok_known_assets_bot/config.example.json
- testingbots/grok_known_assets_bot/tests/
- testingbots/grok_known_assets_bot/docs/GROK_FLOW.md
- testingbots/grok_known_assets_bot/README.md

Current design summary for verification:
- explicit canonical asset allow-list; symbols alone do not authorize non-native assets
- native SOL/Base ETH/Arbitrum ETH enabled; meme placeholders disabled until exact address verification
- host gates: quote freshness, valid bid/ask, reverse sellability, liquidity, 5m volume, spread, impact, positive 15m trend, 5m momentum 0.30%-5%, 1m adverse reversal >= -0.50%, net edge, sizing, breakers, cooldown
- host risk defaults: 0.35% equity risk/trade, 2% max gross, 2 concurrent, 3% chain exposure, 2% daily realised-loss breaker, 3 consecutive losses, 80bps spread, 100bps impact, $250k liquidity, $25k 5m volume, 2.5-4% stop, TP1 +2%, TP2 +4%, 1% trailing drawdown, 60m hold, min net edge 0.60%
- Grok research gate maps host snapshots into a QUALIFY/REJECT scorer with confidence threshold 0.60
- PAPER CLI refuses run unless --paper; there is no signer, private key, broadcast or live adapter
- last isolated deployment test reported 40 tests passing and PAPER_ONLY/live_enabled=false

We are now designing the real data-input layer using CoinGecko, DexScreener, Birdeye, Alchemy/Helius, RugCheck and Jupiter. Please explicitly audit whether the current snapshot schema and scoring logic can safely consume those feeds without hidden unit/provenance/double-counting errors.

Return a structured audit with:
1. VERDICT: PASS / PASS_WITH_FIXES / NOT_READY_FOR_ARMED_PAPER.
2. P0/P1/P2 findings, each with exact file/function/logic affected and why.
3. DATA_INPUT_GAPS: exact identifiers/fields/provenance/freshness timestamps needed from CoinGecko, DexScreener, Birdeye, Alchemy/Helius, RugCheck and Jupiter.
4. DOUBLE_COUNTING_OR_UNIT_RISKS, especially spread/impact/slippage/fee/net-edge and percentage-point vs fraction handling.
5. STRATEGY_LOGIC audit: entry gates, confidence construction, sizing, exits, TP1/trailing/time-stop, multi-asset state, daily breaker semantics.
6. SAFETY audit: canonical asset mapping, reverse sellability, stale quote behavior, provider disagreement/failover, pool safety, kill switch, PAPER/live boundary.
7. TEST_GAPS: concrete tests that must be added before ARMED-PAPER.
8. MINIMUM_FIX_SET before we feed live market data into PAPER mode.
9. ARMED_PAPER_ACCEPTANCE_CRITERIA with objective pass/fail checks.
10. Anything in the design that may reject too many valid opportunities without improving safety.

Do not weaken liquidity, sellability, rug/pool safety, slippage/impact, freshness, capital, signer or simulation safeguards merely to generate trades. Be critical and specific.