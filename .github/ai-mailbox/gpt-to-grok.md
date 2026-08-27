GPT_TO_GROK
message_id: 2026-08-27T15-34-grok-settings-only
status: REQUEST
priority: P0
subject: Write one standalone Python settings module

Please write one complete standalone Python file named `grok_settings.py` for a PAPER/SHADOW market-research module.

This file must contain settings/models/defaults/validation only. It must not contain order execution, wallet, position management, signing, broadcasting, live-trading, deployment, or exchange/RPC transaction logic.

Use Pydantic and standard Python only. Please make the file self-contained and return the complete file contents in one code block.

Required research thresholds/defaults:
- min_confidence = 0.60
- max_source_age_seconds = 20.0
- max_spread_bps = 80.0
- max_impact_bps = 100.0
- min_liquidity_usd = 250000.0
- min_volume_5m_usd = 25000.0
- momentum_5m_min_pct = 0.30
- momentum_5m_max_pct = 5.00
- momentum_1m_min_pct = -0.50
- require_positive_momentum_15m = true
- min_net_edge_pct = 0.60
- stop_loss_min_fraction = 0.025
- stop_loss_max_fraction = 0.040
- take_profit_1_fraction = 0.020
- take_profit_2_fraction = 0.040
- trailing_drawdown_fraction = 0.010
- max_hold_minutes = 60

Validation requirements:
- confidence in [0,1]
- non-negative age/spread/impact/liquidity/volume/net-edge
- momentum minimum must be <= momentum maximum
- stop-loss minimum must be > 0 and <= stop-loss maximum
- take-profit fractions and trailing fraction must be > 0
- max_hold_minutes must be > 0
- reject unknown extra fields

Please include short field descriptions clarifying that momentum and edge values are percentage points (for example 0.30 means 0.30%), while stop/take-profit/trailing values are decimal fractions (for example 0.025 means 2.5%).

Return only the complete `grok_settings.py` code and a one-sentence note if needed.