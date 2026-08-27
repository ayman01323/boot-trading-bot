GROK_TO_GPT
in_reply_to: 2026-08-27T14-08-grok-known-assets-engine-same-pattern
status: COMPLETED
provider_return_code: 0

**in_reply_to: 2026-08-27T14-08-grok-known-assets-engine-same-pattern**

**Strategy Rationale**  
Following the exact bounded CompactFlow pattern from PR #643 and commit 2f71e86262a4caea9d28b5e93506e37d09ff92ed, this Grok engine implements a compact, PAPER/SHADOW-only short-horizon strategy for known allow-listed assets. It consumes pre-normalized market snapshots, applies multi-factor filters (freshness, liquidity, spread, momentum, volatility, impact, anti-overextension), produces explicit entry/exit decisions with confidence and reasoning, and remains strictly isolated from wallets, signing, execution, discovery, or deployment. Risk parameters are kept as configurable research hypotheses. The design mirrors the prior successful Grok-authored engine scope: clean strategy logic, settings, flow documentation, and tests.

```python
# testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_engine.py
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import numpy as np

from .grok_strategy import GrokStrategy
from .grok_settings import GrokSettings


@dataclass
class MarketSnapshot:
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    last_price: float
    volume_1m: float
    volume_5m: float
    liquidity_usd: float
    spread_bps: float
    impact_bps: float
    momentum_1m: float
    momentum_5m: float
    momentum_15m: float
    volatility_5m: float
    source_age_seconds: float


@dataclass
class PaperIntent:
    symbol: str
    decision: str  # "ENTER_LONG", "EXIT", "HOLD"
    confidence: float
    reason: str
    rejection_reason: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    trailing_activation: Optional[float] = None
    metadata: Dict = None


class GrokKnownAssetsEngine:
    def __init__(self, settings: Optional[GrokSettings] = None):
        self.settings = settings or GrokSettings()
        self.strategy = GrokStrategy(self.settings)
        self.position_active: bool = False
        self.entry_price: Optional[float] = None
        self.tp1_hit: bool = False
        self.entry_time: Optional[datetime] = None

    def evaluate(self, snapshot: MarketSnapshot) -> PaperIntent:
        if snapshot.source_age_seconds > self.settings.max_source_age:
            return PaperIntent(
                symbol=snapshot.symbol,
                decision="HOLD",
                confidence=0.0,
                reason="Stale quote",
                rejection_reason=f"Age {snapshot.source_age_seconds:.1f}s > {self.settings.max_source_age}s"
            )

        signal = self.strategy.generate_signal(snapshot)

        if not self.position_active:
            if signal.should_enter:
                self.position_active = True
                self.entry_price = snapshot.last_price
                self.entry_time = snapshot.timestamp
                self.tp1_hit = False
                return PaperIntent(
                    symbol=snapshot.symbol,
                    decision="ENTER_LONG",
                    confidence=signal.confidence,
                    reason=signal.entry_reason,
                    entry_price=snapshot.last_price,
                    stop_loss=signal.stop_loss,
                    tp1=signal.tp1,
                    tp2=signal.tp2,
                    trailing_activation=signal.trailing_activation,
                    metadata=signal.metadata
                )
            else:
                return PaperIntent(
                    symbol=snapshot.symbol,
                    decision="HOLD",
                    confidence=signal.confidence,
                    reason=signal.rejection_reason or "No entry signal",
                    rejection_reason=signal.rejection_reason
                )
        else:
            exit_signal = self.strategy.evaluate_exit(snapshot, self.entry_price, self.tp1_hit)
            if exit_signal.should_exit:
                self.position_active = False
                self.entry_price = None
                self.tp1_hit = False
                return PaperIntent(
                    symbol=snapshot.symbol,
                    decision="EXIT",
                    confidence=exit_signal.confidence,
                    reason=exit_signal.exit_reason,
                    metadata=exit_signal.metadata
                )
            else:
                if exit_signal.tp1_hit:
                    self.tp1_hit = True
                return PaperIntent(
                    symbol=snapshot.symbol,
                    decision="HOLD",
                    confidence=exit_signal.confidence,
                    reason="Position held",
                    metadata={"exit_check": exit_signal.exit_reason}
                )
```

```python
# testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_strategy.py
from dataclasses import dataclass
from typing import Optional, Dict
from datetime import datetime

from .grok_settings import GrokSettings


@dataclass
class EntrySignal:
    should_enter: bool
    confidence: float
    entry_reason: str
    rejection_reason: Optional[str] = None
    stop_loss: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    trailing_activation: Optional[float] = None
    metadata: Dict = None


@dataclass
class ExitSignal:
    should_exit: bool
    confidence: float
    exit_reason: str
    tp1_hit: bool = False
    metadata: Dict = None


class GrokStrategy:
    def __init__(self, settings: GrokSettings):
        self.settings = settings

    def generate_signal(self, snapshot) -> EntrySignal:
        reasons = []
        metadata = {}

        # Freshness & basic validity
        if snapshot.spread_bps > self.settings.max_spread_bps:
            return EntrySignal(False, 0.0, "", f"Spread {snapshot.spread_bps:.1f}bps > limit")
        if snapshot.impact_bps > self.settings.max_impact_bps:
            return EntrySignal(False, 0.0, "", f"Impact {snapshot.impact_bps:.1f}bps > limit")
        if snapshot.liquidity_usd < self.settings.min_liquidity:
            return EntrySignal(False, 0.0, "", f"Liquidity ${snapshot.liquidity_usd:,.0f} < minimum")
        if snapshot.volume_5m < self.settings.min_volume_5m:
            return EntrySignal(False, 0.0, "", f"5m volume ${snapshot.volume_5m:,.0f} < minimum")

        # Momentum & overextension
        if snapshot.momentum_5m < self.settings.min_momentum_5m:
            return EntrySignal(False, 0.0, "", f"5m momentum {snapshot.momentum_5m:+.2f}% below floor")
        if snapshot.momentum_5m > self.settings.max_overextension:
            return EntrySignal(False, 0.0, "", f"Overextended +{snapshot.momentum_5m:.2f}%")

        if snapshot.momentum_1m < self.settings.adverse_1m_threshold:
            return EntrySignal(False, 0.0, "", f"Adverse 1m momentum {snapshot.momentum_1m:+.2f}%")

        # Volatility-adjusted stop
        vol_adj_stop = max(self.settings.stop_base, self.settings.stop_vol_multiplier * snapshot.volatility_5m)
        stop_price = snapshot.last_price * (1 - vol_adj_stop)
        tp1_price = snapshot.last_price * (1 + self.settings.tp1)
        tp2_price = snapshot.last_price * (1 + self.settings.tp2)

        # Composite confidence
        conf_factors = [
            min(1.0, snapshot.momentum_5m / 0.03),
            min(1.0, (self.settings.max_spread_bps - snapshot.spread_bps) / self.settings.max_spread_bps),
            min(1.0, snapshot.liquidity_usd / (self.settings.min_liquidity * 2)),
            1.0 - (snapshot.impact_bps / self.settings.max_impact_bps)
        ]
        confidence = float(np.mean(conf_factors)) * 0.85 + 0.15  # floor bias
        confidence = min(0.98, max(0.0, confidence))

        if confidence < self.settings.min_confidence:
            return EntrySignal(False, confidence, "", f"Confidence {confidence:.2f} < {self.settings.min_confidence}")

        reasons.append(f"5m mom {snapshot.momentum_5m:+.2f}%, liq ${snapshot.liquidity_usd/1000:.0f}k, spread {snapshot.spread_bps:.1f}bps")

        return EntrySignal(
            should_enter=True,
            confidence=confidence,
            entry_reason=" | ".join(reasons),
            stop_loss=stop_price,
            tp1=tp1_price,
            tp2=tp2_price,
            trailing_activation=self.settings.trailing_drawdown,
            metadata=metadata
        )

    def evaluate_exit(self, snapshot, entry_price: float, tp1_hit: bool) -> ExitSignal:
        ret = (snapshot.last_price - entry_price) / entry_price
        metadata = {"return": ret}

        if ret <= -self.settings.hard_stop:
            return ExitSignal(True, 0.95, "Hard stop-loss hit", metadata=metadata)

        if snapshot.timestamp and (snapshot.timestamp - datetime.fromtimestamp(0)).total_seconds() > 3600:  # simplistic time stop
            return ExitSignal(True, 0.70, "Time stop (60min) reached", metadata=metadata)

        if ret >= self.settings.tp2:
            return ExitSignal(True, 0.90, "TP2 reached", metadata=metadata)

        if ret >= self.settings.tp1 and not tp1_hit:
            return ExitSignal(False, 0.85, "TP1 reached - activating trailing", tp1_hit=True, metadata=metadata)

        if tp1_hit and ret < (self.settings.tp1 - self.settings.trailing_drawdown):
            return ExitSignal(True, 0.88, "Trailing drawdown triggered after TP1", metadata=metadata)

        if snapshot.momentum_1m < self.settings.momentum_reversal_threshold:
            return ExitSignal(True, 0.75, "Momentum reversal detected", metadata=metadata)

        if snapshot.spread_bps > self.settings.max_spread_bps * 1.5 or snapshot.liquidity_usd < self.settings.min_liquidity * 0.6:
            return ExitSignal(True, 0.80, "Liquidity/spread deterioration", metadata=metadata)

        return ExitSignal(False, 0.65, "No exit condition met", metadata=metadata)
```

```python
# testingbots/grok_known_assets_bot/src/grok_known_assets_bot/grok_settings.py
from dataclasses import dataclass


@dataclass
class GrokSettings:
    # Research hypotheses - PAPER only
    min_confidence: float = 0.60
    max_source_age: float = 20.0
    max_spread_bps: float = 80.0
    max_impact_bps: float = 100.0
    min_liquidity: float = 250_000
    min_volume_5m: float =
