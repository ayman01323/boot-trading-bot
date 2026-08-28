from __future__ import annotations

from .core import MarketSnapshot, RiskConfig
from .grok_settings import GrokResearchSettings
from .grok_strategy import GrokStrategy, NormalizedObservation, ResearchAssessment


def settings_from_risk(risk: RiskConfig, *, min_confidence: float = 0.60) -> GrokResearchSettings:
    """Mirror the host PAPER risk thresholds into the bounded research schema."""
    return GrokResearchSettings(
        min_confidence=min_confidence,
        max_source_age_seconds=risk.max_quote_age_s,
        max_spread_bps=risk.max_spread_bps,
        max_impact_bps=risk.max_price_impact_bps,
        min_liquidity_usd=risk.min_liquidity_usd,
        min_volume_5m_usd=risk.min_volume_5m_usd,
        momentum_5m_min_pct=risk.momentum_5m_min_pct,
        momentum_5m_max_pct=risk.momentum_5m_max_pct,
        momentum_1m_min_pct=risk.momentum_1m_min_pct,
        momentum_15m_min_pct=risk.momentum_15m_min_pct,
        require_positive_momentum_15m=False,
        min_net_edge_pct=risk.min_net_edge_pct,
        stop_loss_min_fraction=risk.stop_min_pct / 100.0,
        stop_loss_max_fraction=risk.stop_max_pct / 100.0,
        take_profit_1_fraction=risk.take_profit_1_pct / 100.0,
        take_profit_2_fraction=risk.take_profit_2_pct / 100.0,
        trailing_drawdown_fraction=risk.trailing_drawdown_pct / 100.0,
        max_hold_minutes=max(1, int(round(risk.max_hold_minutes))),
    )


def observation_from_snapshot(
    snap: MarketSnapshot,
    risk: RiskConfig,
    *,
    now: float,
) -> NormalizedObservation:
    """Map the normalized host snapshot into Grok's research interface.

    Units are explicit in field names: bps for spread/fees/impact/slippage,
    percentage points for momentum and edge. The mapping reproduces the host's
    round-trip cost model exactly:
        spread + 2*fee + 2*impact + 2*slippage.
    GrokStrategy adds fee + slippage + impact, so the adapter groups one impact
    leg and both slippage legs into estimated_slippage_bps.
    """
    estimated_fee_bps = 2.0 * snap.fee_bps
    estimated_slippage_bps = (
        snap.spread_bps + snap.price_impact_bps + 2.0 * snap.slippage_bps
    )
    return NormalizedObservation(
        canonical_asset_id=snap.asset_key,
        source_age_seconds=max(0.0, float(now) - snap.ts),
        bid=snap.bid,
        ask=snap.ask,
        reverse_sellable=snap.sellable,
        reverse_bid=snap.reverse_bid,
        liquidity_usd=snap.liquidity_usd,
        volume_5m_usd=snap.volume_5m_usd,
        spread_bps=snap.spread_bps,
        impact_bps=snap.price_impact_bps,
        momentum_1m_pct=snap.ret_1m_pct,
        momentum_5m_pct=snap.ret_5m_pct,
        momentum_15m_pct=snap.ret_15m_pct,
        volatility_5m_pct=snap.vol_5m_pct,
        estimated_fee_bps=estimated_fee_bps,
        estimated_slippage_bps=estimated_slippage_bps,
        expected_gross_edge_pct=risk.take_profit_1_pct,
    )


def assess_snapshot(
    snap: MarketSnapshot,
    risk: RiskConfig,
    *,
    now: float,
    min_confidence: float = 0.60,
) -> ResearchAssessment:
    settings = settings_from_risk(risk, min_confidence=min_confidence)
    observation = observation_from_snapshot(snap, risk, now=now)
    return GrokStrategy(settings).assess(observation)
