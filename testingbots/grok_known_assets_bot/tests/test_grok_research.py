from dataclasses import replace

import pytest
from pydantic import ValidationError

from grok_known_assets_bot.grok_settings import GrokResearchSettings
from grok_known_assets_bot.grok_strategy import GrokStrategy, NormalizedObservation


def observation(**overrides):
    values = dict(
        canonical_asset_id="solana:SOL:NATIVE",
        source_age_seconds=1.0,
        bid=99.9,
        ask=100.0,
        reverse_sellable=True,
        reverse_bid=99.8,
        liquidity_usd=500_000.0,
        volume_5m_usd=60_000.0,
        spread_bps=10.0,
        impact_bps=10.0,
        momentum_1m_pct=0.50,
        momentum_5m_pct=2.65,
        momentum_15m_pct=1.00,
        volatility_5m_pct=1.50,
        estimated_fee_bps=10.0,
        estimated_slippage_bps=10.0,
        expected_gross_edge_pct=2.00,
    )
    values.update(overrides)
    return NormalizedObservation(**values)


def test_settings_defaults_and_forbid_extra_fields():
    settings = GrokResearchSettings()
    assert settings.min_confidence == 0.60
    assert settings.max_source_age_seconds == 20.0
    assert settings.momentum_5m_min_pct == 0.30
    assert settings.momentum_5m_max_pct == 5.00
    assert settings.stop_loss_min_fraction == 0.025
    assert settings.take_profit_2_fraction == 0.040
    with pytest.raises(ValidationError):
        GrokResearchSettings(unknown_field=1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_confidence": 1.1},
        {"min_confidence": -0.1},
        {"momentum_5m_min_pct": 6.0, "momentum_5m_max_pct": 5.0},
        {"stop_loss_min_fraction": 0.0},
        {"stop_loss_min_fraction": 0.05, "stop_loss_max_fraction": 0.04},
        {"take_profit_1_fraction": 0.0},
        {"take_profit_2_fraction": -0.01},
        {"trailing_drawdown_fraction": 0.0},
        {"max_hold_minutes": 0},
    ],
)
def test_settings_reject_invalid_values(kwargs):
    with pytest.raises(ValidationError):
        GrokResearchSettings(**kwargs)


def test_strong_observation_qualifies_and_features_are_immutable():
    assessment = GrokStrategy(GrokResearchSettings()).assess(observation())
    assert assessment.label == "QUALIFY"
    assert 0.60 <= assessment.confidence <= 1.0
    assert assessment.reasons == ()
    assert assessment.canonical_asset_id == "solana:SOL:NATIVE"
    with pytest.raises(TypeError):
        assessment.features["freshness_score"] = 0.0


@pytest.mark.parametrize(
    ("changes", "reason_fragment"),
    [
        ({"source_age_seconds": 21.0}, "stale_or_invalid_source_age"),
        ({"bid": 0.0}, "invalid_price"),
        ({"ask": 99.0}, "invalid_price"),
        ({"reverse_sellable": False}, "reverse_not_sellable"),
        ({"reverse_bid": 0.0}, "reverse_not_sellable"),
        ({"liquidity_usd": 249_999.0}, "low_liquidity"),
        ({"volume_5m_usd": 24_999.0}, "low_volume"),
        ({"spread_bps": 81.0}, "wide_or_invalid_spread"),
        ({"impact_bps": 101.0}, "high_or_invalid_impact"),
        ({"momentum_1m_pct": -0.51}, "adverse_1m_momentum"),
        ({"momentum_5m_pct": 0.29}, "momentum_5m_out_of_range"),
        ({"momentum_5m_pct": 5.01}, "momentum_5m_out_of_range"),
        ({"momentum_15m_pct": 0.0}, "non_positive_15m_momentum"),
        ({"estimated_fee_bps": -1.0}, "invalid_negative_cost_component"),
    ],
)
def test_hard_research_gates_reject(changes, reason_fragment):
    assessment = GrokStrategy(GrokResearchSettings()).assess(observation(**changes))
    assert assessment.label == "REJECT"
    assert any(reason_fragment in reason for reason in assessment.reasons)


def test_insufficient_net_edge_rejected():
    assessment = GrokStrategy(GrokResearchSettings()).assess(
        observation(expected_gross_edge_pct=0.50, estimated_fee_bps=10.0, estimated_slippage_bps=10.0, impact_bps=10.0)
    )
    assert assessment.estimated_cost_pct == 0.30
    assert assessment.net_edge_pct == 0.20
    assert assessment.label == "REJECT"
    assert any("net_edge_too_low" in reason for reason in assessment.reasons)


def test_basis_point_cost_conversion_is_exact():
    assessment = GrokStrategy(GrokResearchSettings()).assess(
        observation(
            estimated_fee_bps=30.0,
            estimated_slippage_bps=20.0,
            impact_bps=50.0,
            expected_gross_edge_pct=2.0,
        )
    )
    assert assessment.estimated_cost_pct == 1.0
    assert assessment.net_edge_pct == 1.0


def test_confidence_can_reject_after_all_hard_gates_pass():
    settings = GrokResearchSettings(min_confidence=0.99)
    assessment = GrokStrategy(settings).assess(observation())
    assert assessment.label == "REJECT"
    assert any("confidence_below_min" in reason for reason in assessment.reasons)


def test_zero_thresholds_are_safe_and_do_not_divide_by_zero():
    settings = GrokResearchSettings(
        max_source_age_seconds=0.0,
        max_spread_bps=0.0,
        max_impact_bps=0.0,
        min_liquidity_usd=0.0,
        min_volume_5m_usd=0.0,
        min_net_edge_pct=0.0,
    )
    obs = observation(
        source_age_seconds=0.0,
        spread_bps=0.0,
        impact_bps=0.0,
        liquidity_usd=0.0,
        volume_5m_usd=0.0,
        estimated_fee_bps=0.0,
        estimated_slippage_bps=0.0,
        expected_gross_edge_pct=0.0,
    )
    assessment = GrokStrategy(settings).assess(obs)
    assert assessment.label == "QUALIFY"
    assert 0.0 <= assessment.confidence <= 1.0


def test_positive_15m_requirement_can_be_disabled():
    settings = GrokResearchSettings(require_positive_momentum_15m=False)
    assessment = GrokStrategy(settings).assess(observation(momentum_15m_pct=-2.0))
    assert assessment.label == "QUALIFY"


def test_weight_sum_is_exactly_one():
    assert sum(GrokStrategy._WEIGHTS.values()) == pytest.approx(1.0)
