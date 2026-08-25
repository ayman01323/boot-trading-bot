from decimal import Decimal

from sibot1_engines._shared.contracts import ExitIntent, MarketEvent, TradeIntent
from sibot1_engines.gemini.engine import GeminiPulseFlowEngine
from sibot1_engines.gemini.settings_schema import Settings


def event(**kw):
    d = dict(event_id="e1", chain="solana", observed_at_ms=1000, source="hub", event_type="pool_update", asset_in="USDC", asset_out="SOL", pool_id="p1", price=Decimal("100"), liquidity_usd=Decimal("20000"), volume_usd=Decimal("1000"), source_age_ms=20, payload={"venue":"raydium","volume_velocity":"1.0"})
    d.update(kw); return MarketEvent(**d)


def test_valid_pulse_emits_shared_trade_intent(tmp_path):
    e = GeminiPulseFlowEngine(Settings(), tmp_path)
    out = e.on_market_event(event())
    assert isinstance(out, TradeIntent)
    assert out.engine_id == "gemini" and out.side == "BUY"
    assert out.requested_input_amount == Decimal("1.5")


def test_stale_or_missing_market_data_fails_closed(tmp_path):
    e = GeminiPulseFlowEngine(Settings(), tmp_path)
    assert e.on_market_event(event(source_age_ms=9999)) is None
    assert e.on_market_event(event(liquidity_usd=None)) is None


def test_gemini_exits_only_its_own_lot(tmp_path):
    e = GeminiPulseFlowEngine(Settings(), tmp_path)
    assert e.on_position_update({"engine_id":"gpt","lot_id":"x","pnl_pct":"0.9"}) is None
    out = e.on_position_update({"engine_id":"gemini","lot_id":"g1","chain":"solana","pnl_pct":"0.06","observed_at_ms":2000})
    assert isinstance(out, ExitIntent) and out.lot_id == "g1"
