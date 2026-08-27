from grok_known_assets_bot.core import Asset, Journal, MarketSnapshot, RiskConfig, StrategyEngine
from grok_known_assets_bot.research_adapter import assess_snapshot, observation_from_snapshot


def snapshot(**overrides):
    row = dict(
        asset_key="solana:SOL:NATIVE",
        ts=1000.0,
        bid=99.9,
        ask=100.0,
        reverse_bid=99.9,
        liquidity_usd=500_000.0,
        volume_5m_usd=60_000.0,
        ret_1m_pct=0.50,
        ret_5m_pct=2.65,
        ret_15m_pct=1.00,
        vol_5m_pct=1.50,
        spread_bps=10.0,
        price_impact_bps=10.0,
        fee_bps=5.0,
        sellable=True,
    )
    row.update(overrides)
    return MarketSnapshot.from_dict(row)


def test_adapter_reproduces_host_round_trip_cost_model():
    risk = RiskConfig()
    snap = snapshot()
    obs = observation_from_snapshot(snap, risk, now=snap.ts)
    research_cost = (obs.estimated_fee_bps + obs.estimated_slippage_bps + obs.impact_bps) / 100.0
    host_cost = (snap.spread_bps + 2.0 * snap.fee_bps + 2.0 * snap.price_impact_bps) / 100.0
    assert research_cost == host_cost == 0.40


def test_strong_host_snapshot_qualifies_in_research_layer():
    risk = RiskConfig()
    assessment = assess_snapshot(snapshot(), risk, now=1000.0)
    assert assessment.label == "QUALIFY"
    assert assessment.confidence >= 0.60


def test_research_confidence_can_reject_a_snapshot_that_host_hard_gates_accept(tmp_path):
    risk = RiskConfig()
    snap = snapshot(
        liquidity_usd=risk.min_liquidity_usd,
        volume_5m_usd=risk.min_volume_5m_usd,
        spread_bps=70.0,
        price_impact_bps=20.0,
        ret_1m_pct=-0.49,
        ret_5m_pct=0.30,
        ret_15m_pct=0.01,
        vol_5m_pct=1.0,
    )
    assets = {
        "solana:SOL:NATIVE": Asset(
            key="solana:SOL:NATIVE",
            chain="solana",
            symbol="SOL",
            address="NATIVE",
            enabled=True,
        )
    }
    host = StrategyEngine(assets, risk, Journal(tmp_path / "state.sqlite3"))
    host_decision = host.evaluate_entry(snap, equity=10_000.0, now=snap.ts)
    research = assess_snapshot(snap, risk, now=snap.ts)
    assert host_decision.action == "ENTER"
    assert research.label == "REJECT"
    assert research.confidence < 0.60
    assert any("confidence_below_min" in reason for reason in research.reasons)
