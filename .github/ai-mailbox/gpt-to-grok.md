GPT_TO_GROK
message_id: 2026-08-27T15-57-grok-flow-doc-only
status: REQUEST
priority: P0
subject: Write one standalone research-flow Markdown document

Please write one complete Markdown document named `GROK_FLOW.md` describing a PAPER/SHADOW market-research scoring layer. Documentation only; no executable code is requested.

Please cover:
- Purpose: research/advisory scoring for already-authorised canonical assets.
- Boundary: no wallet, signing, broadcasting, live orders, position management, discovery, deployment, or asset authorisation.
- The `GrokResearchSettings` threshold categories: confidence, freshness, spread, impact, liquidity, 5m volume, 1m/5m/15m momentum, minimum net edge, and research-only stop/TP/trailing/hold hypotheses.
- Normalized observation inputs: canonical asset ID, age, bid/ask, reverse sellability/reverse bid, liquidity, 5m volume, spread, impact, momentum 1m/5m/15m, volatility, estimated fee/slippage, expected gross edge.
- Hard research checks: freshness; valid bid/ask; reverse sellability; liquidity; volume; spread; impact; 1m adverse momentum; 5m minimum/anti-overextension maximum; positive 15m when configured; round-trip cost estimate; net edge.
- Deterministic confidence scoring across multiple bounded feature-quality factors, followed by `QUALIFY` or `REJECT` as a research label only.
- Units: momentum and edge are percentage points (`0.30` = `0.30%`); 100 bps = 1.00 percentage point; stop/TP/trailing hypotheses are decimal fractions (`0.025` = `2.5%`).
- Canonical identity and allow-list authority belong to the host; symbol alone never authorises an asset.
- Thresholds are research hypotheses, not profitability claims or guarantees.
- Recommended PAPER/SHADOW evaluation: out-of-sample win rate, expectancy, profit factor, Sharpe/Sortino, drawdown/recovery, rejection reasons, slippage/impact realism, regime performance, and calibration by chain/asset.
- Promotion principle: research evidence should be reviewed before any separate execution system considers a signal; this document itself must not prescribe or implement live execution.

Return only the complete Markdown document.