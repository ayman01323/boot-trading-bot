from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from .grok_settings import GrokResearchSettings


@dataclass(frozen=True)
class NormalizedObservation:
    canonical_asset_id: str
    source_age_seconds: float
    bid: float
    ask: float
    reverse_sellable: bool
    reverse_bid: float
    liquidity_usd: float
    volume_5m_usd: float
    spread_bps: float
    impact_bps: float
    momentum_1m_pct: float
    momentum_5m_pct: float
    momentum_15m_pct: float
    volatility_5m_pct: float
    estimated_fee_bps: float
    estimated_slippage_bps: float
    expected_gross_edge_pct: float


@dataclass(frozen=True)
class ResearchAssessment:
    canonical_asset_id: str
    label: Literal["QUALIFY", "REJECT"]
    confidence: float
    net_edge_pct: float
    estimated_cost_pct: float
    reasons: tuple[str, ...]
    features: Mapping[str, float]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _minimum_quality(value: float, threshold: float) -> float:
    """Quality for a minimum threshold without division by zero."""
    if threshold <= 0.0:
        return 1.0 if value >= threshold else 0.0
    return _clamp01(value / threshold)


def _maximum_quality(value: float, threshold: float) -> float:
    """Quality for a maximum threshold without division by zero."""
    if threshold <= 0.0:
        return 1.0 if value <= threshold else 0.0
    return _clamp01(1.0 - (value / threshold))


def _range_quality(value: float, lower: float, upper: float) -> float:
    """Triangular quality inside an accepted range, peaking at its midpoint."""
    if upper < lower:
        return 0.0
    if upper == lower:
        return 1.0 if value == lower else 0.0
    if value < lower or value > upper:
        return 0.0
    midpoint = (lower + upper) / 2.0
    half_width = (upper - lower) / 2.0
    return _clamp01(1.0 - abs(value - midpoint) / half_width)


class GrokStrategy:
    """PAPER/SHADOW research scorer; it does not authorise or execute trades."""

    _WEIGHTS: Mapping[str, float] = MappingProxyType(
        {
            "freshness_score": 0.10,
            "liquidity_score": 0.10,
            "volume_score": 0.08,
            "spread_score": 0.10,
            "impact_score": 0.10,
            "momentum_1m_score": 0.10,
            "momentum_5m_score": 0.18,
            "momentum_15m_score": 0.10,
            "edge_score": 0.14,
        }
    )

    def __init__(self, settings: GrokResearchSettings):
        self.settings = settings
        if abs(sum(self._WEIGHTS.values()) - 1.0) > 1e-12:
            raise ValueError("research confidence weights must sum to 1.0")

    def assess(self, obs: NormalizedObservation) -> ResearchAssessment:
        reasons: list[str] = []

        freshness_ok = 0.0 <= obs.source_age_seconds <= self.settings.max_source_age_seconds
        if not freshness_ok:
            reasons.append(f"stale_or_invalid_source_age:{obs.source_age_seconds:.3f}")

        price_ok = obs.bid > 0.0 and obs.ask > 0.0 and obs.ask >= obs.bid
        if not price_ok:
            reasons.append("invalid_price")

        reverse_ok = obs.reverse_sellable and obs.reverse_bid > 0.0
        if not reverse_ok:
            reasons.append("reverse_not_sellable")

        liq_ok = obs.liquidity_usd >= self.settings.min_liquidity_usd
        if not liq_ok:
            reasons.append(f"low_liquidity:{obs.liquidity_usd:.2f}")

        volume_ok = obs.volume_5m_usd >= self.settings.min_volume_5m_usd
        if not volume_ok:
            reasons.append(f"low_volume:{obs.volume_5m_usd:.2f}")

        spread_ok = 0.0 <= obs.spread_bps <= self.settings.max_spread_bps
        if not spread_ok:
            reasons.append(f"wide_or_invalid_spread:{obs.spread_bps:.3f}bps")

        impact_ok = 0.0 <= obs.impact_bps <= self.settings.max_impact_bps
        if not impact_ok:
            reasons.append(f"high_or_invalid_impact:{obs.impact_bps:.3f}bps")

        momentum_1m_ok = obs.momentum_1m_pct >= self.settings.momentum_1m_min_pct
        if not momentum_1m_ok:
            reasons.append(f"adverse_1m_momentum:{obs.momentum_1m_pct:.3f}")

        momentum_5m_ok = (
            self.settings.momentum_5m_min_pct
            <= obs.momentum_5m_pct
            <= self.settings.momentum_5m_max_pct
        )
        if not momentum_5m_ok:
            reasons.append(f"momentum_5m_out_of_range:{obs.momentum_5m_pct:.3f}")

        momentum_15m_ok = (
            not self.settings.require_positive_momentum_15m
            or obs.momentum_15m_pct > 0.0
        )
        if not momentum_15m_ok:
            reasons.append(f"non_positive_15m_momentum:{obs.momentum_15m_pct:.3f}")

        costs_non_negative = (
            obs.estimated_fee_bps >= 0.0
            and obs.estimated_slippage_bps >= 0.0
            and obs.impact_bps >= 0.0
        )
        if not costs_non_negative:
            reasons.append("invalid_negative_cost_component")

        estimated_cost_pct = (
            obs.estimated_fee_bps + obs.estimated_slippage_bps + obs.impact_bps
        ) / 100.0
        net_edge_pct = obs.expected_gross_edge_pct - estimated_cost_pct
        edge_ok = net_edge_pct >= self.settings.min_net_edge_pct
        if not edge_ok:
            reasons.append(f"net_edge_too_low:{net_edge_pct:.4f}")

        momentum_1m_denominator = max(1.0, abs(self.settings.momentum_1m_min_pct) + 1.0)
        momentum_1m_score = _clamp01(
            (obs.momentum_1m_pct - self.settings.momentum_1m_min_pct)
            / momentum_1m_denominator
        )
        if self.settings.require_positive_momentum_15m:
            momentum_15m_score = _clamp01(obs.momentum_15m_pct / 1.0)
        else:
            momentum_15m_score = 1.0

        mutable_features = {
            "freshness_score": _maximum_quality(
                max(0.0, obs.source_age_seconds), self.settings.max_source_age_seconds
            ),
            "liquidity_score": _minimum_quality(
                obs.liquidity_usd, self.settings.min_liquidity_usd
            ),
            "volume_score": _minimum_quality(
                obs.volume_5m_usd, self.settings.min_volume_5m_usd
            ),
            "spread_score": _maximum_quality(
                max(0.0, obs.spread_bps), self.settings.max_spread_bps
            ),
            "impact_score": _maximum_quality(
                max(0.0, obs.impact_bps), self.settings.max_impact_bps
            ),
            "momentum_1m_score": momentum_1m_score,
            "momentum_5m_score": _range_quality(
                obs.momentum_5m_pct,
                self.settings.momentum_5m_min_pct,
                self.settings.momentum_5m_max_pct,
            ),
            "momentum_15m_score": momentum_15m_score,
            "edge_score": _minimum_quality(net_edge_pct, self.settings.min_net_edge_pct),
            "estimated_cost_pct": estimated_cost_pct,
            "net_edge_pct": net_edge_pct,
            "volatility_5m_pct": obs.volatility_5m_pct,
        }

        confidence = _clamp01(
            sum(
                self._WEIGHTS[name] * mutable_features[name]
                for name in self._WEIGHTS
            )
        )

        hard_gates_passed = all(
            (
                freshness_ok,
                price_ok,
                reverse_ok,
                liq_ok,
                volume_ok,
                spread_ok,
                impact_ok,
                momentum_1m_ok,
                momentum_5m_ok,
                momentum_15m_ok,
                costs_non_negative,
                edge_ok,
            )
        )

        if hard_gates_passed and confidence < self.settings.min_confidence:
            reasons.append(f"confidence_below_min:{confidence:.4f}")

        label: Literal["QUALIFY", "REJECT"] = (
            "QUALIFY"
            if hard_gates_passed and confidence >= self.settings.min_confidence
            else "REJECT"
        )

        return ResearchAssessment(
            canonical_asset_id=obs.canonical_asset_id,
            label=label,
            confidence=round(confidence, 4),
            net_edge_pct=round(net_edge_pct, 4),
            estimated_cost_pct=round(estimated_cost_pct, 4),
            reasons=tuple(reasons),
            features=MappingProxyType(dict(mutable_features)),
        )
