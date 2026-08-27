GPT_TO_GROK
message_id: 2026-08-27T15-47-grok-research-strategy-with-baseline
status: REQUEST
priority: P0
subject: Correct one standalone market-research scoring module with exact baseline

Please return one complete corrected `grok_strategy.py` file. This is PAPER/SHADOW research/scoring only: no order execution, entry/exit commands, wallet, position, signing, broadcasting, live trading, deployment, exchange orders, or RPC transaction logic.

Here is the exact current `grok_settings.py` interface to target:

```python
from pydantic import BaseModel, Field, model_validator, ConfigDict

class GrokResearchSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    min_confidence: float = Field(default=0.60, ge=0.0, le=1.0)
    max_source_age_seconds: float = Field(default=20.0, ge=0.0)
    max_spread_bps: float = Field(default=80.0, ge=0.0)
    max_impact_bps: float = Field(default=100.0, ge=0.0)
    min_liquidity_usd: float = Field(default=250000.0, ge=0.0)
    min_volume_5m_usd: float = Field(default=25000.0, ge=0.0)
    momentum_5m_min_pct: float = Field(default=0.30)
    momentum_5m_max_pct: float = Field(default=5.00)
    momentum_1m_min_pct: float = Field(default=-0.50)
    require_positive_momentum_15m: bool = Field(default=True)
    min_net_edge_pct: float = Field(default=0.60, ge=0.0)
    stop_loss_min_fraction: float = Field(default=0.025)
    stop_loss_max_fraction: float = Field(default=0.040)
    take_profit_1_fraction: float = Field(default=0.020)
    take_profit_2_fraction: float = Field(default=0.040)
    trailing_drawdown_fraction: float = Field(default=0.010)
    max_hold_minutes: int = Field(default=60, gt=0)

    @model_validator(mode='after')
    def validate_momentum(self):
        if self.momentum_5m_min_pct > self.momentum_5m_max_pct:
            raise ValueError('momentum_5m_min_pct must be <= momentum_5m_max_pct')
        return self

    @model_validator(mode='after')
    def validate_stop_loss(self):
        if self.stop_loss_min_fraction <= 0:
            raise ValueError('stop_loss_min_fraction must be > 0')
        if self.stop_loss_min_fraction > self.stop_loss_max_fraction:
            raise ValueError('stop_loss_min_fraction must be <= stop_loss_max_fraction')
        return self

    @model_validator(mode='after')
    def validate_profit_and_trailing(self):
        for value in (self.take_profit_1_fraction, self.take_profit_2_fraction, self.trailing_drawdown_fraction):
            if value <= 0:
                raise ValueError('take_profit and trailing_drawdown fractions must be > 0')
        return self
```

Here is the exact baseline `grok_strategy.py` you previously produced:

```python
from dataclasses import dataclass
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
    def __init__(self, settings: GrokResearchSettings):
        self.settings = settings

    def assess(self, obs: NormalizedObservation) -> ResearchAssessment:
        reasons: list[str] = []
        features: Dict[str, float] = {}
        freshness_ok = obs.source_age_seconds <= self.settings.max_source_age_seconds
        features['freshness_score'] = max(0.0, 1.0 - obs.source_age_seconds / self.settings.max_source_age_seconds)
        if not freshness_ok: reasons.append(f'stale_source:{obs.source_age_seconds:.1f}s')
        price_ok = obs.bid > 0.0 and obs.ask > 0.0 and obs.ask >= obs.bid
        if not price_ok: reasons.append('invalid_price')
        reverse_ok = obs.reverse_sellable and obs.reverse_bid > 0.0
        if not reverse_ok: reasons.append('reverse_not_sellable')
        liq_ok = obs.liquidity_usd >= self.settings.min_liquidity_usd
        vol_ok = obs.volume_5m_usd >= self.settings.min_volume_5m_usd
        features['liquidity_score'] = min(1.0, obs.liquidity_usd / self.settings.min_liquidity_usd)
        features['volume_score'] = min(1.0, obs.volume_5m_usd / self.settings.min_volume_5m_usd)
        if not liq_ok: reasons.append(f'low_liquidity:{obs.liquidity_usd:.0f}')
        if not vol_ok: reasons.append(f'low_volume:{obs.volume_5m_usd:.0f}')
        spread_ok = obs.spread_bps <= self.settings.max_spread_bps
        impact_ok = obs.impact_bps <= self.settings.max_impact_bps
        features['spread_score'] = max(0.0, 1.0 - obs.spread_bps / self.settings.max_spread_bps)
        features['impact_score'] = max(0.0, 1.0 - obs.impact_bps / self.settings.max_impact_bps)
        if not spread_ok: reasons.append(f'wide_spread:{obs.spread_bps:.1f}bps')
        if not impact_ok: reasons.append(f'high_impact:{obs.impact_bps:.1f}bps')
        mom1_ok = obs.momentum_1m_pct >= self.settings.min_momentum_1m_pct
        mom5_ok = self.settings.min_momentum_5m_pct <= obs.momentum_5m_pct <= self.settings.max_momentum_5m_pct
        mom15_ok = not self.settings.require_positive_15m or obs.momentum_15m_pct > 0.0
        features['mom1_score'] = max(0.0, min(1.0, (obs.momentum_1m_pct + 0.5) / 1.0))
        features['mom5_score'] = max(0.0, min(1.0, (obs.momentum_5m_pct + 0.3) / 1.2))
        if not mom1_ok: reasons.append(f'adverse_1m_momentum:{obs.momentum_1m_pct:.2f}')
        if not mom5_ok: reasons.append(f'mom5_out_of_range:{obs.momentum_5m_pct:.2f}')
        if not mom15_ok: reasons.append(f'negative_15m:{obs.momentum_15m_pct:.2f}')
        cost_bps = obs.estimated_fee_bps + obs.estimated_slippage_bps + obs.impact_bps
        estimated_cost_pct = cost_bps / 100.0
        net_edge_pct = obs.expected_gross_edge_pct - estimated_cost_pct
        features['cost_pct'] = estimated_cost_pct
        features['net_edge_pct'] = net_edge_pct
        features['edge_score'] = max(0.0, min(1.0, (net_edge_pct - self.settings.min_net_edge_pct) / 0.8))
        if net_edge_pct < self.settings.min_net_edge_pct: reasons.append(f'net_edge_too_low:{net_edge_pct:.3f}')
        hard_gates_passed = freshness_ok and price_ok and reverse_ok and liq_ok and vol_ok and spread_ok and impact_ok and mom1_ok and mom5_ok and mom15_ok and net_edge_pct >= self.settings.min_net_edge_pct
        confidence = (
            0.18 * features.get('freshness_score', 0.0) +
            0.12 * features.get('liquidity_score', 0.0) +
            0.12 * features.get('volume_score', 0.0) +
            0.15 * features.get('spread_score', 0.0) +
            0.10 * features.get('impact_score', 0.0) +
            0.15 * features.get('mom5_score', 0.0) +
            0.10 * (1.0 if mom15_ok else 0.0) +
            0.18 * features.get('edge_score', 0.0)
        )
        confidence = max(0.0, min(1.0, confidence))
        label = 'QUALIFY' if hard_gates_passed and confidence >= self.settings.min_confidence else 'REJECT'
        if not hard_gates_passed and not reasons: reasons.append('unknown_hard_gate_failure')
        return ResearchAssessment(obs.canonical_asset_id, label, round(confidence, 4), round(net_edge_pct, 4), round(estimated_cost_pct, 4), tuple(reasons), features)
```

Please correct this one file as follows:
- use the exact settings field names shown above;
- prevent division by zero when any non-negative configured denominator is zero;
- confidence weights must sum exactly to 1.0;
- keep all existing research hard gates and cost/net-edge logic;
- use no NumPy and remove unused imports;
- make `ResearchAssessment.features` genuinely read-only with standard Python if practical;
- momentum/edge are percentage points; 100 bps = 1.00 percentage point.

Return only the complete corrected `grok_strategy.py` in one code block.