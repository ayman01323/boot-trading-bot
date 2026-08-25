from decimal import Decimal

from sibot1_engines._shared.contracts import ExitIntent, MarketEvent, TradeIntent
from sibot1_engines._shared.solana_dev_flow import DeveloperFlowEvidence
from sibot1_engines.grok.engine import GrokCompactFlowEngine
from sibot1_engines.grok.settings_schema import Settings


class FakeResolver:
    def __init__(self, evidence: DeveloperFlowEvidence):
        self.evidence = evidence
        self.calls = []

    def resolve(self, mint: str) -> DeveloperFlowEvidence:
        self.calls.append(mint)
        return self.evidence


def flow(*, known: bool, selling: bool, reason: str = "test") -> DeveloperFlowEvidence:
    return DeveloperFlowEvidence(
        known=known,
        selling=selling,
        dev_wallet="DEV",
        source="test",
        reason=reason,
        checked_at_ms=1,
        coverage_complete=known,
    )


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
    resolver = FakeResolver(flow(known=False, selling=False))
    engine = GrokCompactFlowEngine(Settings(), tmp_path, resolver)
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
    assert resolver.calls == ["TOKEN"]


def test_feed_unknown_can_be_enriched_to_known_not_selling(tmp_path):
    resolver = FakeResolver(flow(known=True, selling=False, reason="covered no sale"))
    engine = GrokCompactFlowEngine(Settings(), tmp_path, resolver)
    out = engine.on_market_event(
        ev(payload={
            "confidence": "0.9",
            "volume_velocity": "3",
            "dev_selling_known": False,
            "dev_selling": False,
        })
    )
    assert isinstance(out, TradeIntent)
    assert out.metadata["dev_selling_known"] is True
    assert out.metadata["dev_selling"] is False
    assert resolver.calls == ["TOKEN"]


def test_feed_unknown_enriched_to_active_selling_is_blocked(tmp_path):
    resolver = FakeResolver(flow(known=True, selling=True, reason="sale observed"))
    engine = GrokCompactFlowEngine(Settings(), tmp_path, resolver)
    assert engine.on_market_event(
        ev(payload={
            "confidence": "0.9",
            "volume_velocity": "3",
            "dev_selling_known": False,
            "dev_selling": False,
        })
    ) is None
    assert engine.health()["developer_flow_selling"] == 1


def test_exit_ownership(tmp_path):
    engine = GrokCompactFlowEngine(Settings(), tmp_path, FakeResolver(flow(known=False, selling=False)))
    assert engine.on_position_update({"engine_id": "gemini", "lot_id": "x", "pnl_pct": "9"}) is None
    opportunity = engine.on_position_update({
        "engine_id": "grok",
        "lot_id": "g1",
        "pnl_pct": "0.04",
        "chain": "solana",
    })
    assert isinstance(opportunity, ExitIntent)
    assert opportunity.lot_id == "g1"
