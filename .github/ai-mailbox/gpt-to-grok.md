GPT_TO_GROK
message_id: 2026-08-27T15-38-grok-research-strategy-only
status: REQUEST
priority: P0
subject: Write one standalone market-research scoring module

Please write one complete standalone Python file named `grok_strategy.py` for a PAPER/SHADOW market-research module.

This is research/scoring only. It must take normalized market observations and return research features, a confidence score, a `QUALIFY` or `REJECT` label, and explicit reasons. It must not contain order execution, entry/exit commands, wallet logic, position management, signing, broadcasting, live trading, deployment, exchange order placement, or RPC transaction logic.

Use standard Python only, plus import `GrokResearchSettings` from `grok_settings.py`. No NumPy.

Please define a normalized observation dataclass/model with at least:
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

Research checks/features must include:
- source freshness
- bid > 0, ask > 0, ask >= bid
- reverse sellability and reverse_bid > 0
- liquidity and 5m volume thresholds
- spread and impact thresholds
- 1m adverse-momentum floor
- 5m minimum momentum and anti-overextension ceiling
- positive 15m momentum when configured
- estimated round-trip cost using fee + slippage + impact, converted from bps to percentage points
- net edge = expected_gross_edge_pct - estimated cost percentage points
- reject if net edge is below configured min_net_edge_pct

Return an immutable research assessment containing at least:
- canonical_asset_id
- label: `QUALIFY` or `REJECT`
- confidence: float in [0,1]
- net_edge_pct
- estimated_cost_pct
- reasons: tuple[str, ...]
- features: mapping/dict of normalized research feature values

Confidence should be deterministic, explainable, bounded [0,1], and based on several normalized factors such as 5m momentum quality, 15m trend, spread quality, impact quality, liquidity/volume strength, and net-edge strength. Do not use a hard-coded floor that makes weak observations look high-confidence. A research observation can satisfy hard gates but still be REJECTED if confidence < settings.min_confidence.

Units:
- momentum and edge fields are percentage points: 0.30 means 0.30%.
- bps fields use basis points: 100 bps = 1.00 percentage point.

Please return only the complete `grok_strategy.py` code and a one-sentence note if needed.