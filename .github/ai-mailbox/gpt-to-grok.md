GPT_TO_GROK
message_id: 2026-08-27T15-52-grok-fresh-research-strategy
status: REQUEST
priority: P0
subject: Write one fresh standalone market-research scoring module

Please write a NEW complete standalone Python file named `grok_strategy.py` for a PAPER/SHADOW market-research module. Do not preserve, repair, or reference any previous implementation. Use only the exact settings interface below.

This is research/scoring only. It must take normalized market observations and return research features, a confidence score, a `QUALIFY` or `REJECT` label, and explicit reasons. It must not contain order execution, entry/exit commands, wallet logic, position management, signing, broadcasting, live trading, deployment, exchange order placement, or RPC transaction logic.

The only settings attributes this scorer may reference are:
- `min_confidence`
- `max_source_age_seconds`
- `max_spread_bps`
- `max_impact_bps`
- `min_liquidity_usd`
- `min_volume_5m_usd`
- `momentum_5m_min_pct`
- `momentum_5m_max_pct`
- `momentum_1m_min_pct`
- `require_positive_momentum_15m`
- `min_net_edge_pct`

Import `GrokResearchSettings` from `grok_settings.py`. Use standard Python only; no NumPy.

Define a frozen normalized observation with:
- canonical_asset_id: str
- source_age_seconds: float
- bid: float
- ask: float
- reverse_sellable: bool
- reverse_bid: float
- liquidity_usd: float
- volume_5m_usd: float
- spread_bps: float
- impact_bps: float
- momentum_1m_pct: float
- momentum_5m_pct: float
- momentum_15m_pct: float
- volatility_5m_pct: float
- estimated_fee_bps: float
- estimated_slippage_bps: float
- expected_gross_edge_pct: float

Hard research checks:
- source_age_seconds <= max_source_age_seconds
- bid > 0, ask > 0, ask >= bid
- reverse_sellable is true and reverse_bid > 0
- liquidity_usd >= min_liquidity_usd
- volume_5m_usd >= min_volume_5m_usd
- spread_bps <= max_spread_bps
- impact_bps <= max_impact_bps
- momentum_1m_pct >= momentum_1m_min_pct
- momentum_5m_min_pct <= momentum_5m_pct <= momentum_5m_max_pct
- when require_positive_momentum_15m is true, momentum_15m_pct must be > 0
- estimated_cost_pct = (estimated_fee_bps + estimated_slippage_bps + impact_bps) / 100.0
- net_edge_pct = expected_gross_edge_pct - estimated_cost_pct
- net_edge_pct >= min_net_edge_pct

Return a frozen `ResearchAssessment` containing:
- canonical_asset_id
- label (`QUALIFY` or `REJECT`)
- confidence in [0,1]
- net_edge_pct
- estimated_cost_pct
- reasons as tuple[str, ...]
- features as a read-only Mapping[str, float], preferably using MappingProxyType

Confidence requirements:
- deterministic and explainable
- no artificial high-confidence floor
- combine multiple bounded quality features such as freshness, liquidity, volume, spread, impact, 5m momentum quality, 15m trend quality, and net-edge quality
- feature weights must sum exactly to 1.00
- hard gates can all pass yet final label must still be REJECT when confidence < min_confidence

Zero-threshold safety:
Settings allow zero for age/spread/impact/liquidity/volume/net-edge thresholds. Do not divide by zero. Use safe helper functions. For a zero minimum threshold, a non-negative observed value may score full quality. For a zero maximum threshold, score full quality only when the observed value is also zero; otherwise score zero.

Units:
- momentum and edge fields are percentage points: 0.30 means 0.30%.
- bps fields are basis points: 100 bps = 1.00 percentage point.

Return only the complete `grok_strategy.py` code in one code block.