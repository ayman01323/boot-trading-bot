from decimal import Decimal

from sibot1_engines._shared.contracts import ExitIntent, MarketEvent, TradeIntent
from sibot1_engines.grok.engine import GrokCompactFlowEngine
from sibot1_engines.grok.settings_schema import Settings


def ev(**kw):
    data = dict(
        event_id="e",
        chain="solana",
        observed_at_ms=1,
        source="hub",
        event_type="pool_update",
        asset_in="USDC",
        asset_out="TOKEN",
        price=Decimal("1"),
        source_age_ms=10,
        payload={
            "venue": "raydium",
            "confidence": "0.75",
            "volume_velocity": "2",
            "dev_selling_known": True,
            "dev_selling": False,
        },
    )
    data.update(kw)
    return MarketEvent(**data)


def test_signal_and_dev_filter(tmp_path):
    engine = GrokCompactFlowEngine(Settings(), tmp_path)
    opportunity = engine.on_market_event(ev())
    assert isinstance(opportunity, TradeIntent)
    assert opportunity.engine_id == "grok"

    assert engine.on_market_event(
        ev(payload={
            "confidence": "0.9",
            "volume_velocity": "3",
            "dev_selling_known": True,
            "dev_selling": True,
        })
    ) is None

    # Missing/unknown developer-flow evidence is not equivalent to a safe "not selling" signal.
    assert engine.on_market_event(
        ev(payload={
            "confidence": "0.9",
            "volume_velocity": "3",
            "dev_selling_known": False,
            "dev_selling": False,
        })
    ) is None


def test_exit_ownership(tmp_path):
    engine = GrokCompactFlowEngine(Settings(), tmp_path)
    assert engine.on_position_update({"engine_id": "gemini", "lot_id": "x", "pnl_pct": "9"}) is None
    opportunity = engine.on_position_update({
        "engine_id": "grok",
        "lot_id": "g1",
        "pnl_pct": "0.04",
        "chain": "solana",
    })
    assert isinstance(opportunity, ExitIntent)
    assert opportunity.lot_id == "g1"
