from decimal import Decimal

from sibot1_engines._shared.contracts import MarketEvent, TradeIntent
from sibot1_engines.gemini.engine import GeminiPulseFlowEngine
from sibot1_engines.gemini.settings_schema import Settings


def event(*, event_id="e1", observed_at_ms=1_000, asset_out="TOKEN", liquidity="20000", volume="1000", payload=None):
    return MarketEvent(
        event_id=event_id,
        chain="solana",
        observed_at_ms=observed_at_ms,
        source="hub",
        event_type="market_pulse",
        asset_in="SOL",
        asset_out=asset_out,
        price=Decimal("1"),
        liquidity_usd=Decimal(liquidity),
        volume_usd=Decimal(volume),
        source_age_ms=10,
        payload=payload or {"volume_velocity": "1", "liquidity_velocity": "0"},
    )


def test_same_mint_is_not_reemitted_each_market_pulse(tmp_path):
    engine = GeminiPulseFlowEngine(Settings(), tmp_path)
    assert isinstance(engine.on_market_event(event()), TradeIntent)
    assert engine.on_market_event(event(event_id="e2", observed_at_ms=10_000)) is None
    assert engine.health()["prefilter_rejections"]["signal_cooldown"] == 1


def test_volume_liquidity_ratio_rejects_dead_or_wash_extremes(tmp_path):
    engine = GeminiPulseFlowEngine(Settings(), tmp_path)
    assert engine.on_market_event(event(asset_out="LOW", liquidity="20000", volume="100")) is None
    assert engine.on_market_event(event(asset_out="HIGH", liquidity="10000", volume="200000")) is None
    health = engine.health()["prefilter_rejections"]
    assert health["volume_floor"] == 1 or health["volume_liquidity_ratio_low"] == 1
    assert health["volume_liquidity_ratio_high"] == 1


def test_attached_structural_risk_evidence_is_rejected_before_poolcheck(tmp_path):
    engine = GeminiPulseFlowEngine(Settings(), tmp_path)
    assert engine.on_market_event(event(asset_out="MINTAUTH", payload={
        "volume_velocity": "1",
        "liquidity_velocity": "0",
        "mint_authority_present": True,
    })) is None
    assert engine.on_market_event(event(asset_out="FREEZE", payload={
        "volume_velocity": "1",
        "liquidity_velocity": "0",
        "freeze_authority_present": True,
    })) is None
    assert engine.on_market_event(event(asset_out="UNLOCKED", payload={
        "volume_velocity": "1",
        "liquidity_velocity": "0",
        "lp_locked_pct": "10",
    })) is None
    health = engine.health()["prefilter_rejections"]
    assert health["mint_authority_active"] == 1
    assert health["freeze_authority_active"] == 1
    assert health["lp_lock_prefilter"] == 1


def test_unknown_structural_fields_do_not_bypass_central_poolcheck(tmp_path):
    engine = GeminiPulseFlowEngine(Settings(), tmp_path)
    out = engine.on_market_event(event(asset_out="UNKNOWN", payload={
        "volume_velocity": "1",
        "liquidity_velocity": "0",
    }))
    assert isinstance(out, TradeIntent)
    # The strategy only prefilters. The runtime still routes every emitted intent
    # through MandatoryShadowPoolCheck before any paper entry is accepted.
