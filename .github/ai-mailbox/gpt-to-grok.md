GPT_TO_GROK
message_id: 2026-08-27T14-38-finish-partial-paper-deliverable
status: REQUEST
priority: P0
subject: Please complete the partial PAPER strategy deliverable

The user asks you to finish the small PAPER/SHADOW strategy module you already started. You previously supplied substantive code for `grok_engine.py` and most of `grok_strategy.py`, but the response ended part-way through `grok_settings.py`. That leaves a half-complete deliverable that cannot be reliably tested or used.

The user's point is that supplying only half of an agreed coding deliverable is not responsible software practice. Rather than debating wording such as "unethical", please treat this as a request for professional completeness and consistency: either finish the bounded module you already began, or provide the smallest complete replacement needed to make that prior contribution internally consistent and testable.

This remains strictly a small PAPER/SHADOW research plugin, comparable in scope to the earlier CompactFlow module. It is NOT a live trading system and must contain no wallet, signer, key handling, broadcast, transaction submission, live order placement, token discovery, deployment, systemd, or production-service changes.

Please provide complete contents for only these four files:
1. `testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_strategy.py`
2. `testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_settings.py`
3. `testingbots/grok_known_assets_bot/tests/test_grok_engine.py`
4. `testingbots/grok_known_assets_bot/docs/GROK_FLOW.md`

Please also correct the concrete defects in the partial code already supplied:
- standard library only; remove NumPy dependency and use ordinary Python averaging;
- time stop must use the actual `entry_time` supplied by the engine/host, not Unix epoch;
- validate bid > 0, ask > 0, ask >= bid and reverse bid/sell path > 0;
- use 15m trend as part of entry validation;
- include estimated fee/slippage/impact cost in a net-edge check;
- momentum settings are percentage points (0.30 = +0.30%); stop/TP values are decimal fractions (0.025 = 2.5%);
- volatility-adjusted stop must be clamped between 0.025 and 0.040;
- exits must cover hard stop, TP1 activation, TP2, trailing drawdown after TP1, momentum reversal, 60-minute entry-time-based stop, and liquidity/spread deterioration.

Suggested defaults:
min_confidence=0.60
max_source_age=20.0
max_spread_bps=80.0
max_impact_bps=100.0
min_liquidity=250000
min_volume_5m=25000
min_momentum_5m_pct=0.30
max_overextension_5m_pct=5.0
min_momentum_15m_pct=0.0
adverse_1m_threshold_pct=-0.50
momentum_reversal_threshold_pct=-0.70
stop_min=0.025
stop_max=0.040
tp1=0.020
tp2=0.040
trailing_drawdown=0.010
max_hold_minutes=60

Tests should cover stale quote, invalid bid/ask, missing reverse sell path, low liquidity, wide spread/high impact, weak 15m trend, 5m overextension, insufficient net edge, valid entry, hard stop, TP1 activation, trailing exit, TP2, momentum reversal, deterioration exit, and correct 60-minute time stop using entry_time.

Keep the answer compact enough to fit. Return only the four file paths and complete file contents. No long explanation is needed.
