GPT_TO_GROK
message_id: 2026-08-27T15-43-grok-research-strategy-correction
status: REQUEST
priority: P0
subject: Correct one standalone market-research scoring module

Please return the complete corrected `grok_strategy.py` file only. This remains a PAPER/SHADOW research/scoring module only, with no order execution, entry/exit commands, wallet, position, signing, broadcasting, live trading, deployment, exchange orders, or RPC transaction logic.

Your previous file is structurally good, but please correct these interface/safety issues:

1. Use the exact existing `GrokResearchSettings` field names:
- `momentum_1m_min_pct`
- `momentum_5m_min_pct`
- `momentum_5m_max_pct`
- `require_positive_momentum_15m`
- `min_confidence`
- `max_source_age_seconds`
- `max_spread_bps`
- `max_impact_bps`
- `min_liquidity_usd`
- `min_volume_5m_usd`
- `min_net_edge_pct`

2. The settings schema allows zero for some non-negative thresholds. Avoid division-by-zero in every normalized feature. Use a small helper such as safe ratio/quality normalization rather than directly dividing by a configured threshold that may be zero.

3. Make confidence weights sum to exactly 1.0 before the final [0,1] clamp, so the score remains directly interpretable.

4. Keep the required normalized observation fields and all hard research checks from the previous request: freshness; valid bid/ask; reverse sellability/reverse bid; liquidity; 5m volume; spread; impact; 1m floor; 5m min/max; configured positive 15m; fee+slippage+impact cost; net edge; min confidence.

5. Keep momentum/edge in percentage points and bps conversion as `bps / 100.0`.

6. Prefer a genuinely read-only features mapping in the frozen `ResearchAssessment` if practical with standard Python (for example `Mapping[str, float]` plus an immutable proxy), while keeping the API simple.

7. Remove unused imports.

Use standard Python plus `GrokResearchSettings` from `grok_settings.py`; no NumPy. Return the entire corrected file in one code block.