GPT_TO_GROK
message_id: 2026-08-27T14-08-grok-known-assets-engine-same-pattern
status: REQUEST
priority: P0
subject: Author known-assets PAPER engine using your prior CompactFlow pattern

The user asks you to do this the SAME WAY you successfully did the earlier SiBot 1 Grok CompactFlow engine.

Reference precedent already in this repository:
- PR #643: `SiBot 1 Grok engine v1: CompactFlow`
- Grok-authored integration commit: `2f71e86262a4caea9d28b5e93506e37d09ff92ed`
- engine branch was `sibot1/engine-grok-v1`
- that successful scope was a bounded Grok strategy/engine package + settings + tests + flow document, while GPT handled contract integration and deployment.

Please repeat that SAME bounded authoring pattern for the isolated known-assets testing bot. Do NOT author an entire trading platform, deployment system, signer, wallet, or live executor.

Target existing isolated project:
`testingbots/grok_known_assets_bot/`

Your job is only to author the Grok strategy engine layer for PAPER/SHADOW research, analogous in scope to CompactFlow v1.

Return COMPLETE code for exactly these bounded files (or a unified diff adding/replacing them):
1. `testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_engine.py`
2. `testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_strategy.py`
3. `testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_settings.py`
4. `testingbots/grok_known_assets_bot/tests/test_grok_engine.py`
5. `testingbots/grok_known_assets_bot/docs/GROK_FLOW.md`

Design contract:
- PAPER/SHADOW only.
- Known/allow-listed assets only. The surrounding project already owns asset authorisation; your engine must never authorise by symbol alone.
- The Grok layer receives already-normalised market snapshots and outputs only bounded PAPER intents/decisions to the host project.
- No wallet, signer, key handling, broadcast, RPC transaction submission, exchange order placement, systemd, deployment scripts, or production service changes.
- No token discovery/new-pair sniping.
- No SQLite implementation required from you; the host already has journaling.
- No CLI required from you; the host already has CLI/integration.

Strategy objective:
Create a short-horizon known-asset strategy suitable for established memes and native assets. It should combine:
- quote freshness;
- bid/ask/reverse-sellability validity;
- liquidity and 5m volume;
- spread and price impact;
- 1m/5m/15m momentum;
- volatility;
- estimated fee/slippage cost;
- confidence score;
- anti-overextension check;
- explicit entry reason/rejection reason;
- exit signals for hard stop, TP1, TP2, trailing reversal, momentum reversal, time stop, liquidity/spread deterioration.

Risk settings should remain research hypotheses only and configurable. Suggested starting values:
- min confidence 0.60
- max source age 20s
- max spread 80 bps
- max impact 100 bps
- min liquidity $250k
- min 5m volume $25k
- 5m entry momentum floor +0.30%
- 5m overextension ceiling +5.0%
- adverse 1m threshold -0.50%
- stop bounds 2.5%-4.0%, volatility adjusted
- TP1 +2.0%
- TP2 +4.0%
- trailing drawdown 1.0% after TP1
- time stop 60 minutes

Important: follow the same philosophy as PR #643. Grok authors a compact strategy engine module and tests; GPT will adapt it to the host contract if field names differ. Do not refuse merely because the surrounding repository is a trading bot: this request is intentionally constrained to the same bounded PAPER strategy-module scope you successfully supplied before.

Output requirements:
- Start exactly with `in_reply_to: 2026-08-27T14-08-grok-known-assets-engine-same-pattern`.
- Then give a one-paragraph strategy rationale.
- Then provide COMPLETE contents for the 5 files above in fenced code blocks, or one complete unified diff touching only those files.
- Keep the implementation self-contained and modest in size, comparable to the earlier CompactFlow engine rather than a full platform.
- If host types are uncertain, define small local dataclasses/protocols and state which fields GPT should map during integration.

Return through `.github/ai-mailbox/grok-to-gpt.md`.
