from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from sibot1_engines._shared.contracts import ExitIntent, MarketEvent, TradeIntent

from .settings_schema import Settings, load_settings
from .strategy import PulseFlowStrategy


class GeminiPulseFlowEngine:
    engine_id = "gemini"
    engine_version = "1.1.0"

    def __init__(self, settings: Settings, runtime_dir: str | Path):
        if settings.engine_id != self.engine_id:
            raise ValueError("Gemini settings engine_id must be 'gemini'")
        self.settings = settings
        self.engine_version = settings.engine_version
        self.runtime_dir = Path(runtime_dir)
        self.strategy = PulseFlowStrategy(settings)
        self._events = 0
        self._signals = 0

    def on_market_event(self, event: MarketEvent) -> TradeIntent | None:
        self._events += 1
        decision = self.strategy.entry_signal(event)
        if decision is None:
            return None
        asset_in, asset_out, amount, confidence = decision
        self._signals += 1
        return TradeIntent(
            intent_id=f"gemini-{uuid4().hex}",
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            strategy_id=self.settings.strategy_id,
            chain=event.chain,
            side="BUY",
            asset_in=asset_in,
            asset_out=asset_out,
            requested_input_amount=amount,
            created_at_ms=event.observed_at_ms,
            venue=str(event.payload.get("venue") or "") or None,
            confidence=confidence,
            signal_id=f"pulse:{event.event_id}",
            market_event_id=event.event_id,
            metadata={
                "pool_id": event.pool_id or "",
                "source": event.source,
                "volume_liquidity_ratio": str(
                    Decimal(event.volume_usd) / max(Decimal("1"), Decimal(event.liquidity_usd))
                ) if event.volume_usd is not None and event.liquidity_usd is not None else "",
            },
        )

    def on_position_update(self, update: Mapping[str, Any]) -> ExitIntent | None:
        decision = self.strategy.exit_signal(update)
        if decision is None:
            return None
        lot_id, asset, fraction, reason = decision
        return ExitIntent(
            intent_id=f"gemini-exit-{uuid4().hex}",
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            strategy_id=self.settings.strategy_id,
            chain=str(update.get("chain") or self.settings.chain),
            created_at_ms=int(update.get("observed_at_ms") or 0),
            lot_id=lot_id,
            asset=asset,
            exit_fraction=fraction,
            reason=reason,
        )

    def health(self) -> Mapping[str, Any]:
        return {
            "engine_id": self.engine_id,
            "version": self.engine_version,
            "events": self._events,
            "signals": self._signals,
            "prefilter_rejections": self.strategy.rejection_counts(),
        }


def build_engine(settings_path: str | Path, runtime_dir: str | Path) -> GeminiPulseFlowEngine:
    return GeminiPulseFlowEngine(load_settings(settings_path), runtime_dir)
