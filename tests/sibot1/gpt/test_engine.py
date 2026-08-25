from decimal import Decimal

from sibot1_engines._shared.contracts import MarketEvent, TradeIntent
from sibot1_engines.gpt.engine import GPTNetEdgeArbEngine
from sibot1_engines.gpt.settings_schema import Settings


def _event(**overrides):
    data = dict(
        event_id="e1", chain="base", observed_at_ms=1000, source="test",
        event_type="dex_spread", asset_in="USDC", asset_out="WETH",
        payload={"gross_edge_bps":"30","estimated_cost_bps":"10","quote_age_ms":50,"buy_venue":"aerodrome","sell_venue":"uniswap_v3"},
    )
    data.update(overrides)
    return MarketEvent(**data)


def test_profitable_fresh_spread_emits_arbitrage_intent(tmp_path):
    engine = GPTNetEdgeArbEngine(Settings(), tmp_path)
    out = engine.on_market_event(_event())
    assert isinstance(out, TradeIntent)
    assert out.engine_id == "gpt"
    assert out.side == "ARBITRAGE"
    assert out.route_hint == ("aerodrome", "uniswap_v3")
    assert out.expected_net_profit == Decimal("0.01")
    assert out.metadata["atomic_required"] is True


def test_stale_or_unprofitable_spread_is_ignored(tmp_path):
    engine = GPTNetEdgeArbEngine(Settings(), tmp_path)
    stale = _event(payload={"gross_edge_bps":"30","estimated_cost_bps":"10","quote_age_ms":9999,"buy_venue":"a","sell_venue":"b"})
    weak = _event(payload={"gross_edge_bps":"15","estimated_cost_bps":"10","quote_age_ms":10,"buy_venue":"a","sell_venue":"b"})
    assert engine.on_market_event(stale) is None
    assert engine.on_market_event(weak) is None


def test_engine_never_exits_another_engine_lot(tmp_path):
    engine = GPTNetEdgeArbEngine(Settings(), tmp_path)
    assert engine.on_position_update({"engine_id":"gemini","lot_id":"lot-x","age_ms":99999,"pnl_pct":"-9"}) is None
