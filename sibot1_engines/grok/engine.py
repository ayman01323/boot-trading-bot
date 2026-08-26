from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from sibot1_engines._shared.contracts import ExitIntent, MarketEvent, TradeIntent
from sibot1_engines._shared.solana_dev_flow import SolanaDeveloperFlowResolver
from .settings_schema import Settings, load_settings
from .strategy import CompactFlowStrategy


class GrokCompactFlowEngine:
    engine_id = "grok"
    engine_version = "1.0.1"

    def __init__(
        self,
        settings: Settings,
        runtime_dir: str | Path,
        dev_flow_resolver: SolanaDeveloperFlowResolver | None = None,
    ):
        self.settings = settings
        self.runtime_dir = Path(runtime_dir)
        self.strategy = CompactFlowStrategy(settings)
        self.dev_flow = dev_flow_resolver or SolanaDeveloperFlowResolver()
        self._events = 0
        self._signals = 0
        self._dev_known_safe = 0
        self._dev_selling = 0
        self._dev_unknown = 0

    def _enrich_dev_flow(self, event: MarketEvent) -> MarketEvent:
        if event.chain.lower() != "solana" or not event.asset_out:
            return event
        if bool(event.payload.get("dev_selling_known", False)):
            return event
        evidence = self.dev_flow.resolve(str(event.asset_out))
        payload = {**dict(event.payload), **evidence.as_payload()}
        if evidence.known and evidence.selling:
            self._dev_selling += 1
        elif evidence.known:
            self._dev_known_safe += 1
        else:
            self._dev_unknown += 1
        return replace(event, payload=payload)

    def on_market_event(self, event: MarketEvent) -> TradeIntent | None:
        self._events += 1
        if self.settings.reject_dev_selling:
            event = self._enrich_dev_flow(event)
        confidence = self.strategy.entry_signal(event)
        if confidence is None:
            return None
        self._signals += 1
        return TradeIntent(
            intent_id=f"grok-{uuid4().hex}",
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            strategy_id=self.settings.strategy_id,
            chain=event.chain,
            side="BUY",
            asset_in=str(event.asset_in),
            asset_out=str(event.asset_out),
            requested_input_amount=self.settings.trade_amount,
            created_at_ms=event.observed_at_ms,
            venue=str(event.payload.get("venue") or "") or None,
            confidence=confidence,
            signal_id=f"compact:{event.event_id}",
            market_event_id=event.event_id,
            metadata={
                "dev_selling_known": bool(event.payload.get("dev_selling_known", False)),
                "dev_selling": bool(event.payload.get("dev_selling", False)),
                "dev_selling_source": str(event.payload.get("dev_selling_source") or ""),
                "dev_selling_reason": str(event.payload.get("dev_selling_reason") or ""),
                "volume_velocity": str(event.payload.get("volume_velocity", "0")),
                "pool_id": event.pool_id or "",
            },
        )

    def on_position_update(self, update: Mapping[str, Any]) -> ExitIntent | None:
        d = self.strategy.exit_signal(update)
        if d is None:
            return None
        lot_id, fraction, reason = d
        return ExitIntent(
            intent_id=f"grok-exit-{uuid4().hex}",
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            strategy_id=self.settings.strategy_id,
            chain=str(update.get("chain") or self.settings.chain),
            created_at_ms=int(update.get("observed_at_ms") or 0),
            lot_id=lot_id,
            exit_fraction=fraction,
            reason=reason,
        )

    def health(self) -> Mapping[str, Any]:
        return {
            "engine_id": self.engine_id,
            "version": self.engine_version,
            "events": self._events,
            "signals": self._signals,
            "developer_flow_known_safe": self._dev_known_safe,
            "developer_flow_selling": self._dev_selling,
            "developer_flow_unknown": self._dev_unknown,
            "prefilter_rejections": self.strategy.rejection_counts(),
        }


def build_engine(settings_path: str | Path, runtime_dir: str | Path) -> GrokCompactFlowEngine:
    return GrokCompactFlowEngine(load_settings(settings_path), runtime_dir)
