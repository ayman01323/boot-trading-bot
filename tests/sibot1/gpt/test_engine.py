from decimal import Decimal

from sibot1_engines._shared.contracts import MarketEvent, TradeIntent
from sibot1_engines._shared.solana_dev_flow import DeveloperFlowEvidence
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


class _DevResolver:
    def __init__(self, *, known=True, selling=False, lp_locked_pct="60"):
        self.known = known
        self.selling = selling
        self.lp_locked_pct = lp_locked_pct

    def resolve(self, mint):
        return DeveloperFlowEvidence(
            known=self.known,
            selling=self.selling,
            dev_wallet="DEV" if self.known else None,
            source="test",
            reason="test",
            checked_at_ms=1000,
            coverage_complete=self.known,
            mint_authority_present=False,
            freeze_authority_present=False,
            lp_locked_pct=self.lp_locked_pct,
        )


def _sol_event(**overrides):
    data = dict(
        event_id="sol-1",
        chain="solana",
        observed_at_ms=1000,
        source="test",
        event_type="market_pulse",
        asset_in="USDC",
        asset_out="SOL_MINT",
        price=Decimal("0.001"),
        liquidity_usd=Decimal("30000"),
        volume_usd=Decimal("10000"),
        source_age_ms=0,
        payload={
            "confidence": "0.80",
            "leader_event_age_ms": 30000,
            "liquidity_velocity": "0",
            "volume_velocity": "0",
            "venue": "raydium",
            "dev_selling_known": False,
            "dev_selling": False,
        },
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


def test_gpt_solana_uses_first_sample_quality_without_positive_velocity(tmp_path):
    engine = GPTNetEdgeArbEngine(Settings(), tmp_path, dev_flow_resolver=_DevResolver())
    out = engine.on_market_event(_sol_event())
    assert isinstance(out, TradeIntent)
    assert out.engine_id == "gpt"
    assert out.chain == "solana"
    assert out.side == "BUY"
    assert out.asset_in == "SOL"
    assert out.asset_out == "SOL_MINT"
    assert out.requested_input_amount == Decimal("1")
    assert out.metadata["execution_family"] == "SOLANA_LEADER_QUALITY"
    assert out.metadata["live_revalidation_required"] is True
    assert engine.health()["solana_signals"] == 1


def test_gpt_solana_rejects_unsafe_or_weak_candidates(tmp_path):
    selling = GPTNetEdgeArbEngine(Settings(), tmp_path, dev_flow_resolver=_DevResolver(selling=True))
    assert selling.on_market_event(_sol_event()) is None
    assert selling.health()["prefilter_rejections"]["developer_selling"] == 1

    weak = GPTNetEdgeArbEngine(Settings(), tmp_path, dev_flow_resolver=_DevResolver())
    assert weak.on_market_event(_sol_event(liquidity_usd=Decimal("5000"))) is None
    assert weak.health()["prefilter_rejections"]["liquidity_floor"] == 1


def test_gpt_solana_exit_has_positive_reward_risk_and_time_stop(tmp_path):
    engine = GPTNetEdgeArbEngine(Settings(), tmp_path, dev_flow_resolver=_DevResolver())
    tp = engine.on_position_update({
        "engine_id":"gpt", "lot_id":"lot-sol", "chain":"solana",
        "age_ms":10000, "pnl_pct":"0.031", "observed_at_ms":2000,
    })
    assert tp is not None and tp.reason == "take_profit"

    sl = engine.on_position_update({
        "engine_id":"gpt", "lot_id":"lot-sol", "chain":"solana",
        "age_ms":10000, "pnl_pct":"-0.016", "observed_at_ms":2000,
    })
    assert sl is not None and sl.reason == "stop_loss"

    timed = engine.on_position_update({
        "engine_id":"gpt", "lot_id":"lot-sol", "chain":"solana",
        "age_ms":180000, "pnl_pct":"0.001", "observed_at_ms":2000,
    })
    assert timed is not None and timed.reason == "time_stop"


def test_engine_never_exits_another_engine_lot(tmp_path):
    engine = GPTNetEdgeArbEngine(Settings(), tmp_path)
    assert engine.on_position_update({"engine_id":"gemini","lot_id":"lot-x","age_ms":99999,"pnl_pct":"-9"}) is None
