message_id: 2026-08-27T14-30-known-assets-paper-module-finish
status: REQUEST
priority: P0
subject: Finish four Python files for bounded PAPER strategy module

Please complete a small Python PAPER/SHADOW research strategy module in an existing repository. This is an ordinary coding task, not a request to assume any identity, simulate an internal role, or claim repository access.

Context: an earlier response already supplied part of this bounded module but was truncated. The host application already provides authorisation, journaling, CLI, deployment, and any integration. Your task is only to supply the missing strategy-layer code and tests.

Return complete contents for these four files only:
1. `testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_strategy.py`
2. `testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_settings.py`
3. `testingbots/grok_known_assets_bot/tests/test_grok_engine.py`
4. `testingbots/grok_known_assets_bot/docs/GROK_FLOW.md`

Module scope:
- PAPER/SHADOW only.
- Known/allow-listed assets only; the host performs allow-list checks.
- No wallet, signer, keys, broadcast, live order placement, transaction submission, token discovery, deployment, systemd, or production-service code.
- Inputs are already-normalised market snapshots.
- Outputs are only bounded research entry/exit decisions.

Correctness requirements:
- standard library only; do not require NumPy.
- validate bid > 0, ask > 0, ask >= bid, reverse bid/sell path > 0.
- reject stale data older than configurable max age (default 20s).
- liquidity, 5m volume, spread, and impact gates.
- require positive 15m trend and configurable 5m entry momentum floor.
- reject 5m overextension above configurable ceiling.
- reject sufficiently adverse 1m momentum.
- include estimated fees/slippage/impact in a net-edge check before entry.
- momentum settings use percentage points (example: 0.30 means +0.30%).
- stop/TP values use decimal fractions (example: 0.025 means 2.5%).
- volatility-adjusted stop must be clamped to 2.5%-4.0%.
- exits: hard stop, TP1 activation, TP2, trailing drawdown after TP1, 1m momentum reversal, 60-minute time stop based on actual entry_time passed in by host, and liquidity/spread deterioration.

Suggested defaults:
- min_confidence 0.60
- max_source_age 20.0
- max_spread_bps 80.0
- max_impact_bps 100.0
- min_liquidity 250000
- min_volume_5m 25000
- min_momentum_5m_pct 0.30
- max_overextension_5m_pct 5.0
- min_momentum_15m_pct 0.0
- adverse_1m_threshold_pct -0.50
- momentum_reversal_threshold_pct -0.70
- stop_min 0.025
- stop_max 0.040
- stop_vol_multiplier configurable
- tp1 0.020
- tp2 0.040
- trailing_drawdown 0.010
- max_hold_minutes 60
- fee_bps configurable
- assumed_slippage_bps configurable
- min_net_edge_pct configurable

Tests must cover at least:
- stale quote
- invalid bid/ask
- missing reverse sell path
- low liquidity
- wide spread/high impact
- weak 15m trend
- 5m overextension
- insufficient net edge
- valid entry
- hard stop
- TP1 activation
- trailing exit
- TP2 exit
- momentum reversal
- deterioration exit
- correct 60-minute time stop using entry_time

Keep the implementation compact, similar in size to a small strategy plugin rather than a trading platform. Return the four file paths followed by complete file contents in fenced code blocks. No long narrative is needed.
