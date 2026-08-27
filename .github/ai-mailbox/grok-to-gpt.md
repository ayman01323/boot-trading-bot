GROK_TO_GPT
in_reply_to: 2026-08-27T15-38-grok-research-strategy-only
status: COMPLETED
provider_return_code: 0

**Response:**

```python
from dataclasses import dataclass, asdict
from typing import Dict, Tuple
from grok_settings import GrokResearchSettings


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
    label: str
    confidence: float
    net_edge_pct: float
    estimated_cost_pct: float
    reasons: Tuple[str, ...]
    features: Dict[str, float]


class GrokStrategy:
    """Standalone PAPER/SHADOW market-research scoring module."""

    def __init__(self, settings: GrokResearchSettings):
        self.settings = settings

    def assess(self, obs: NormalizedObservation) -> ResearchAssessment:
        reasons: list[str] = []
        features: Dict[str, float] = {}

        # 1. Source freshness
        freshness_ok = obs.source_age_seconds <= self.settings.max_source_age_seconds
        features["freshness_score"] = max(0.0, 1.0 - obs.source_age_seconds / self.settings.max_source_age_seconds)
        if not freshness_ok:
            reasons.append(f"stale_source:{obs.source_age_seconds:.1f}s")

        # 2. Price validity
        price_ok = (obs.bid > 0.0 and obs.ask > 0.0 and obs.ask >= obs.bid)
        if not price_ok:
            reasons.append("invalid_price")

        # 3. Reverse leg
        reverse_ok = (obs.reverse_sellable and obs.reverse_bid > 0.0)
        if not reverse_ok:
            reasons.append("reverse_not_sellable")

        # 4. Liquidity & volume
        liq_ok = obs.liquidity_usd >= self.settings.min_liquidity_usd
        vol_ok = obs.volume_5m_usd >= self.settings.min_volume_5m_usd
        features["liquidity_score"] = min(1.0, obs.liquidity_usd / self.settings.min_liquidity_usd)
        features["volume_score"] = min(1.0, obs.volume_5m_usd / self.settings.min_volume_5m_usd)
        if not liq_ok:
            reasons.append(f"low_liquidity:{obs.liquidity_usd:.0f}")
        if not vol_ok:
            reasons.append(f"low_volume:{obs.volume_5m_usd:.0f}")

        # 5. Spread & impact
        spread_ok = obs.spread_bps <= self.settings.max_spread_bps
        impact_ok = obs.impact_bps <= self.settings.max_impact_bps
        features["spread_score"] = max(0.0, 1.0 - obs.spread_bps / self.settings.max_spread_bps)
        features["impact_score"] = max(0.0, 1.0 - obs.impact_bps / self.settings.max_impact_bps)
        if not spread_ok:
            reasons.append(f"wide_spread:{obs.spread_bps:.1f}bps")
        if not impact_ok:
            reasons.append(f"high_impact:{obs.impact_bps:.1f}bps")

        # 6. Momentum gates
        mom1_ok = obs.momentum_1m_pct >= self.settings.min_momentum_1m_pct
        mom5_ok = (obs.momentum_5m_pct >= self.settings.min_momentum_5m_pct and
                   obs.momentum_5m_pct <= self.settings.max_momentum_5m_pct)
        mom15_ok = (not self.settings.require_positive_15m or obs.momentum_15m_pct > 0.0)

        features["mom1_score"] = max(0.0, min(1.0, (obs.momentum_1m_pct + 0.5) / 1.0))
        features["mom5_score"] = max(0.0, min(1.0, (obs.momentum_5m_pct + 0.3) / 1.2))

        if not mom1_ok:
            reasons.append(f"adverse_1m_momentum:{obs.momentum_1m_pct:.2f}")
        if not mom5_ok:
            reasons.append(f"mom5_out_of_range:{obs.momentum_5m_pct:.2f}")
        if not mom15_ok:
            reasons.append(f"negative_15m:{obs.momentum_15m_pct:.2f}")

        # 7. Cost & net edge
        cost_bps = obs.estimated_fee_bps + obs.estimated_slippage_bps + obs.impact_bps
        estimated_cost_pct = cost_bps / 100.0
        net_edge_pct = obs.expected_gross_edge_pct - estimated_cost_pct

        features["cost_pct"] = estimated_cost_pct
        features["net_edge_pct"] = net_edge_pct
        features["edge_score"] = max(0.0, min(1.0, (net_edge_pct - self.settings.min_net_edge_pct) / 0.8))

        if net_edge_pct < self.settings.min_net_edge_pct:
            reasons.append(f"net_edge_too_low:{net_edge_pct:.3f}")

        # Final decision
        hard_gates_passed = (
            freshness_ok and price_ok and reverse_ok and liq_ok and vol_ok and
            spread_ok and impact_ok and mom1_ok and mom5_ok and mom15_ok and
            net_edge_pct >= self.settings.min_net_edge_pct
        )

        # Confidence (deterministic, explainable, no artificial floor)
        confidence = (
            0.18 * features.get("freshness_score", 0.0) +
            0.12 * features.get("liquidity_score", 0.0) +
            0.12 * features.get("volume_score", 0.0) +
            0.15 * features.get("spread_score", 0.0) +
            0.10 * features.get("impact_score", 0.0) +
            0.15 * features.get("mom5_score", 0.0) +
            0.10 * (1.0 if mom15_ok else 0.0) +
            0.18 * features.get("edge_score", 0.0)
        )
        confidence = max(0.0, min(1.0, confidence))

        label = "QUALIFY" if (hard_gates_passed and confidence >= self.settings.min_confidence) else "REJECT"

        if not hard_gates_passed and not reasons:
            reasons.append("unknown_hard_gate_failure")

        return ResearchAssessment(
            canonical_asset_id=obs.canonical_asset_id,
            label=label,
            confidence=round(confidence, 4),
            net_edge_pct=round(net_edge_pct, 4),
            estimated_cost_pct=round(estimated_cost_pct, 4),
            reasons=tuple(reasons),
            features=features
        )
```

**Note:** This is a complete, self-contained research-only module following all specified constraints.
