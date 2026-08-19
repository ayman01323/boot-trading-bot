from decimal import Decimal

import pytest

from learnerbot.cross_chain_strategy_signals import MarketFeatures, evaluate_all, forecasted_positive_net_edge


def _features(chain_type: str) -> MarketFeatures:
    return MarketFeatures(
        chain_type=chain_type,
        chain_slug="solana" if chain_type == "SOLANA" else "base",
        asset="TEST_ASSET",
        observed_at=1_700_000_000,
        gross_edge_bps=Decimal("25"),
        fees_bps=Decimal("2"),
        slippage_bps=Decimal("2"),
        price_impact_bps=Decimal("1"),
        latency_reserve_bps=Decimal("1"),
        liquidity_score=Decimal("0.9"),
        sellability_score=Decimal("0.98"),
        holder_or_flow_dispersion=Decimal("0.8"),
        route_replicability=Decimal("0.85"),
        momentum_z=Decimal("2.0"),
        flow_acceleration_z=Decimal("2.0"),
        dislocation_z=Decimal("2.0"),
        volatility_z=Decimal("1.0"),
        quote_age_ms=500,
        pool_age_seconds=3600,
        independent_wallet_count=8,
        learned_route_avg_net_bps=Decimal("12"),
        forecast_positive_edge_probability=Decimal("0.78"),
        forecast_expected_net_bps=Decimal("15"),
        forecast_uncertainty=Decimal("0.15"),
    )


def test_same_strategy_logic_runs_on_solana_and_evm():
    sol = evaluate_all(_features("SOLANA"))
    evm = evaluate_all(_features("EVM"))
    assert [x.strategy for x in sol] == [x.strategy for x in evm]
    assert [x.eligible for x in sol] == [x.eligible for x in evm]
    assert [x.score for x in sol] == [x.score for x in evm]
    assert all(x.mode == "SHADOW" for x in sol + evm)
    assert all(x.chain_type in {"SOLANA", "EVM"} for x in sol + evm)


def test_costs_can_turn_apparent_gross_edge_into_abstention():
    f = _features("EVM")
    costly = MarketFeatures(**{
        **f.__dict__,
        "gross_edge_bps": Decimal("10"),
        "fees_bps": Decimal("4"),
        "slippage_bps": Decimal("4"),
        "price_impact_bps": Decimal("3"),
        "latency_reserve_bps": Decimal("2"),
    })
    assert costly.net_edge_bps < 0
    signals = evaluate_all(costly)
    assert not any(s.eligible for s in signals)
    assert all(any("net_edge_below" in r for r in s.reasons) for s in signals)


def test_forecast_abstains_when_uncertain_even_with_positive_current_edge():
    f = _features("SOLANA")
    uncertain = MarketFeatures(**{
        **f.__dict__,
        "forecast_positive_edge_probability": Decimal("0.80"),
        "forecast_expected_net_bps": Decimal("16"),
        "forecast_uncertainty": Decimal("0.60"),
    })
    decision = forecasted_positive_net_edge(uncertain)
    assert decision.eligible is False
    assert "forecast_uncertainty_too_high_abstain" in decision.reasons


def test_forecast_requires_current_executable_edge_not_model_alone():
    f = _features("EVM")
    no_current_edge = MarketFeatures(**{
        **f.__dict__,
        "gross_edge_bps": Decimal("4"),
        "fees_bps": Decimal("2"),
        "slippage_bps": Decimal("2"),
        "price_impact_bps": Decimal("1"),
        "latency_reserve_bps": Decimal("1"),
        "forecast_positive_edge_probability": Decimal("0.95"),
        "forecast_expected_net_bps": Decimal("30"),
        "forecast_uncertainty": Decimal("0.05"),
    })
    decision = forecasted_positive_net_edge(no_current_edge)
    assert decision.eligible is False
    assert any("net_edge_below" in r for r in decision.reasons)


def test_invalid_chain_type_is_rejected():
    f = _features("EVM")
    invalid = MarketFeatures(**{**f.__dict__, "chain_type": "BTC"})
    with pytest.raises(ValueError):
        evaluate_all(invalid)
