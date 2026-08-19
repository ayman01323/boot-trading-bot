from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable


# This module is deliberately pure and SHADOW-only.  It contains no wallet, signer,
# RPC submission or live-executor imports.  Both Solana and EVM feature adapters can
# feed the same economic strategy rules after computing chain-specific execution costs.


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _clip(value, low=Decimal("0"), high=Decimal("1")) -> Decimal:
    d = _d(value)
    return max(low, min(high, d))


@dataclass(frozen=True)
class MarketFeatures:
    chain_type: str
    chain_slug: str
    asset: str
    observed_at: int

    # All economic edge/cost values are in basis points of notional.
    gross_edge_bps: Decimal = Decimal("0")
    fees_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    price_impact_bps: Decimal = Decimal("0")
    latency_reserve_bps: Decimal = Decimal("0")

    # Normalised quality measures: 0 poor, 1 strong.
    liquidity_score: Decimal = Decimal("0")
    sellability_score: Decimal = Decimal("0")
    holder_or_flow_dispersion: Decimal = Decimal("0")
    route_replicability: Decimal = Decimal("0")

    # Standardised/relative signal measures. Positive momentum/flow means acceleration;
    # dislocation may be positive or negative because mean reversion is two-sided.
    momentum_z: Decimal = Decimal("0")
    flow_acceleration_z: Decimal = Decimal("0")
    dislocation_z: Decimal = Decimal("0")
    volatility_z: Decimal = Decimal("0")

    # Quote/pool context.
    quote_age_ms: int = 0
    pool_age_seconds: int = 0
    independent_wallet_count: int = 0

    # Learned/forecast evidence calculated strictly from information available before
    # the decision.  These may be absent/zero until a feature adapter has enough data.
    learned_route_avg_net_bps: Decimal = Decimal("0")
    forecast_positive_edge_probability: Decimal = Decimal("0")
    forecast_expected_net_bps: Decimal = Decimal("0")
    forecast_uncertainty: Decimal = Decimal("1")

    @property
    def net_edge_bps(self) -> Decimal:
        return (
            _d(self.gross_edge_bps)
            - _d(self.fees_bps)
            - _d(self.slippage_bps)
            - _d(self.price_impact_bps)
            - _d(self.latency_reserve_bps)
        )

    def validate(self) -> None:
        if str(self.chain_type or "").upper() not in {"SOLANA", "EVM"}:
            raise ValueError("chain_type must be SOLANA or EVM")
        if not str(self.chain_slug or "").strip() or not str(self.asset or "").strip():
            raise ValueError("chain_slug and asset are required")
        if int(self.quote_age_ms) < 0 or int(self.pool_age_seconds) < 0 or int(self.independent_wallet_count) < 0:
            raise ValueError("ages/counts cannot be negative")
        for name in (
            "liquidity_score", "sellability_score", "holder_or_flow_dispersion",
            "route_replicability", "forecast_positive_edge_probability", "forecast_uncertainty",
        ):
            value = _d(getattr(self, name))
            if value < 0 or value > 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class StrategySignal:
    strategy: str
    chain_type: str
    chain_slug: str
    asset: str
    mode: str
    eligible: bool
    score: Decimal
    expected_net_edge_bps: Decimal
    confidence: Decimal
    reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("score", "expected_net_edge_bps", "confidence"):
            out[key] = str(out[key])
        return out


def _signal(features: MarketFeatures, strategy: str, *, eligible: bool, score, confidence, reasons) -> StrategySignal:
    features.validate()
    return StrategySignal(
        strategy=strategy,
        chain_type=str(features.chain_type).upper(),
        chain_slug=str(features.chain_slug),
        asset=str(features.asset),
        mode="SHADOW",
        eligible=bool(eligible),
        score=_clip(score),
        expected_net_edge_bps=features.net_edge_bps,
        confidence=_clip(confidence),
        reasons=tuple(str(x) for x in reasons),
    )


def _common_executable(features: MarketFeatures, *, min_net_bps=Decimal("3"), max_quote_age_ms=2500) -> tuple[bool, list[str]]:
    reasons = []
    if features.net_edge_bps < _d(min_net_bps):
        reasons.append(f"net_edge_below_{min_net_bps}bps")
    if _d(features.liquidity_score) < Decimal("0.55"):
        reasons.append("liquidity_below_minimum")
    if _d(features.sellability_score) < Decimal("0.80"):
        reasons.append("sellability_below_minimum")
    if int(features.quote_age_ms) > int(max_quote_age_ms):
        reasons.append("quote_too_old")
    return not reasons, reasons


def cross_venue_net_arbitrage(features: MarketFeatures) -> StrategySignal:
    ok, reasons = _common_executable(features, min_net_bps=Decimal("4"), max_quote_age_ms=1500)
    score = _clip(features.net_edge_bps / Decimal("25")) * Decimal("0.55") + _clip(features.liquidity_score) * Decimal("0.45")
    return _signal(features, "Cross Venue Net Arbitrage", eligible=ok, score=score, confidence=score, reasons=reasons or ["positive_executable_cross_venue_edge"])


def liquidity_confirmed_momentum(features: MarketFeatures) -> StrategySignal:
    ok, reasons = _common_executable(features, min_net_bps=Decimal("5"), max_quote_age_ms=2000)
    if _d(features.momentum_z) < Decimal("1.0"):
        reasons.append("momentum_not_confirmed")
    if _d(features.flow_acceleration_z) < Decimal("0.5"):
        reasons.append("flow_not_confirmed")
    if _d(features.liquidity_score) < Decimal("0.65"):
        reasons.append("liquidity_confirmation_missing")
    score = (
        _clip(_d(features.momentum_z) / Decimal("3")) * Decimal("0.35")
        + _clip(_d(features.flow_acceleration_z) / Decimal("3")) * Decimal("0.25")
        + _clip(features.liquidity_score) * Decimal("0.25")
        + _clip(features.net_edge_bps / Decimal("30")) * Decimal("0.15")
    )
    return _signal(features, "Liquidity Confirmed Momentum", eligible=not reasons, score=score, confidence=score, reasons=reasons or ["momentum_confirmed_by_flow_liquidity_and_net_edge"])


def dislocation_mean_reversion(features: MarketFeatures) -> StrategySignal:
    ok, reasons = _common_executable(features, min_net_bps=Decimal("4"), max_quote_age_ms=2500)
    if abs(_d(features.dislocation_z)) < Decimal("1.5"):
        reasons.append("dislocation_too_small")
    if _d(features.volatility_z) > Decimal("3.0"):
        reasons.append("volatility_too_extreme_for_reversion")
    if _d(features.liquidity_score) < Decimal("0.65"):
        reasons.append("liquidity_not_stable_enough")
    score = (
        _clip(abs(_d(features.dislocation_z)) / Decimal("4")) * Decimal("0.45")
        + _clip(features.liquidity_score) * Decimal("0.30")
        + _clip(features.net_edge_bps / Decimal("25")) * Decimal("0.25")
    )
    return _signal(features, "Dislocation Mean Reversion", eligible=not reasons, score=score, confidence=score, reasons=reasons or ["cost_adjusted_dislocation_with_liquidity_support"])


def flow_acceleration(features: MarketFeatures) -> StrategySignal:
    ok, reasons = _common_executable(features, min_net_bps=Decimal("4"), max_quote_age_ms=2000)
    if _d(features.flow_acceleration_z) < Decimal("1.2"):
        reasons.append("flow_acceleration_too_low")
    if int(features.independent_wallet_count) < 3:
        reasons.append("insufficient_independent_flow_sources")
    if _d(features.holder_or_flow_dispersion) < Decimal("0.45"):
        reasons.append("flow_too_concentrated")
    score = (
        _clip(_d(features.flow_acceleration_z) / Decimal("4")) * Decimal("0.45")
        + _clip(features.holder_or_flow_dispersion) * Decimal("0.25")
        + _clip(features.liquidity_score) * Decimal("0.15")
        + _clip(features.net_edge_bps / Decimal("25")) * Decimal("0.15")
    )
    return _signal(features, "Flow Acceleration", eligible=not reasons, score=score, confidence=score, reasons=reasons or ["independent_flow_acceleration_with_positive_net_edge"])


def new_liquidity_quality(features: MarketFeatures) -> StrategySignal:
    ok, reasons = _common_executable(features, min_net_bps=Decimal("6"), max_quote_age_ms=1500)
    if int(features.pool_age_seconds) <= 0 or int(features.pool_age_seconds) > 86400:
        reasons.append("pool_not_in_new_liquidity_window")
    if _d(features.liquidity_score) < Decimal("0.75"):
        reasons.append("new_pool_liquidity_quality_too_low")
    if _d(features.sellability_score) < Decimal("0.95"):
        reasons.append("new_asset_sellability_not_strong_enough")
    if _d(features.holder_or_flow_dispersion) < Decimal("0.50"):
        reasons.append("new_asset_flow_too_concentrated")
    score = (
        _clip(features.liquidity_score) * Decimal("0.30")
        + _clip(features.sellability_score) * Decimal("0.25")
        + _clip(features.holder_or_flow_dispersion) * Decimal("0.20")
        + _clip(features.net_edge_bps / Decimal("35")) * Decimal("0.25")
    )
    return _signal(features, "New Liquidity Quality", eligible=not reasons, score=score, confidence=score, reasons=reasons or ["new_pool_quality_sellability_dispersion_and_edge_confirmed"])


def learned_route_replication(features: MarketFeatures) -> StrategySignal:
    ok, reasons = _common_executable(features, min_net_bps=Decimal("4"), max_quote_age_ms=2000)
    if _d(features.route_replicability) < Decimal("0.70"):
        reasons.append("route_replicability_below_minimum")
    if _d(features.learned_route_avg_net_bps) <= 0:
        reasons.append("learned_route_not_historically_net_positive")
    score = (
        _clip(features.route_replicability) * Decimal("0.45")
        + _clip(_d(features.learned_route_avg_net_bps) / Decimal("25")) * Decimal("0.25")
        + _clip(features.net_edge_bps / Decimal("25")) * Decimal("0.30")
    )
    return _signal(features, "Learned Route Replication", eligible=not reasons, score=score, confidence=score, reasons=reasons or ["replicable_route_requoted_with_positive_current_net_edge"])


def forecasted_positive_net_edge(features: MarketFeatures) -> StrategySignal:
    ok, reasons = _common_executable(features, min_net_bps=Decimal("4"), max_quote_age_ms=2000)
    p = _d(features.forecast_positive_edge_probability)
    uncertainty = _d(features.forecast_uncertainty)
    if p < Decimal("0.65"):
        reasons.append("forecast_probability_below_threshold")
    if _d(features.forecast_expected_net_bps) < Decimal("4"):
        reasons.append("forecast_expected_net_edge_too_low")
    if uncertainty > Decimal("0.35"):
        reasons.append("forecast_uncertainty_too_high_abstain")
    # Current executable net edge remains mandatory even when a model predicts profit.
    score = (
        _clip(p) * Decimal("0.45")
        + _clip(_d(features.forecast_expected_net_bps) / Decimal("30")) * Decimal("0.25")
        + (Decimal("1") - _clip(uncertainty)) * Decimal("0.15")
        + _clip(features.net_edge_bps / Decimal("25")) * Decimal("0.15")
    )
    confidence = _clip(p * (Decimal("1") - _clip(uncertainty)))
    return _signal(features, "Forecasted Positive Net Edge", eligible=not reasons, score=score, confidence=confidence, reasons=reasons or ["calibrated_forecast_and_current_executable_edge_agree"])


STRATEGY_EVALUATORS: tuple[Callable[[MarketFeatures], StrategySignal], ...] = (
    cross_venue_net_arbitrage,
    liquidity_confirmed_momentum,
    dislocation_mean_reversion,
    flow_acceleration,
    new_liquidity_quality,
    learned_route_replication,
    forecasted_positive_net_edge,
)


def evaluate_all(features: MarketFeatures) -> list[StrategySignal]:
    features.validate()
    return [fn(features) for fn in STRATEGY_EVALUATORS]
