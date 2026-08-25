from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from sibot1_engines._shared.contracts import ExitIntent, MarketEvent, TradeIntent

from .settings_schema import Settings, load_settings
from .strategy import score_spread


class GPTNetEdgeArbEngine:
    engine_id = "gpt"
    engine_version = "1.0.0"

    def __init__(self, settings: Settings, runtime_dir: str | Path):
        self.settings = settings
        self.runtime_dir = Path(runtime_dir)
        self._events = 0
        self._signals = 0

    def on_market_event(self, event: MarketEvent) -> TradeIntent | None:
        self._events += 1
        if event.chain.lower() != self.settings.chain.lower() or event.event_type != "dex_spread":
            return None
        score = score_spread(event.payload, self.settings)
        if score is None:
            return None
        asset_in = str(event.asset_in or event.payload.get("asset_in") or "").strip()
        asset_out = str(event.asset_out or event.payload.get("asset_out") or "").strip()
        if not asset_in or not asset_out:
            return None
        self._signals += 1
        requested = self.settings.trade_size_quote
        expected_net = requested * Decimal(score["net_edge_bps"]) / Decimal("10000")
        return TradeIntent(
            intent_id=f"gpt-{uuid4().hex}",
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            strategy_id=self.settings.strategy_id,
            chain=event.chain,
            side="ARBITRAGE",
            asset_in=asset_in,
            asset_out=asset_out,
            requested_input_amount=requested,
            created_at_ms=event.observed_at_ms,
            venue="cross-dex",
            route_hint=(score["buy_venue"], score["sell_venue"]),
            expected_net_profit=expected_net,
            signal_id=f"spread:{event.event_id}",
            market_event_id=event.event_id,
            metadata={
                "gross_edge_bps": str(score["gross_edge_bps"]),
                "estimated_cost_bps": str(score["estimated_cost_bps"]),
                "net_edge_bps": str(score["net_edge_bps"]),
                "atomic_required": True,
            },
        )

    def on_position_update(self, update: Mapping[str, Any]) -> ExitIntent | None:
        if str(update.get("engine_id") or "") != self.engine_id:
            return None
        lot_id = str(update.get("lot_id") or "").strip()
        if not lot_id:
            return None
        age_ms = int(update.get("age_ms") or 0)
        pnl_pct = Decimal(str(update.get("pnl_pct") or "0"))
        if age_ms < self.settings.max_open_ms and pnl_pct > -self.settings.emergency_loss_pct:
            return None
        return ExitIntent(
            intent_id=f"gpt-exit-{uuid4().hex}",
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            strategy_id=self.settings.strategy_id,
            chain=str(update.get("chain") or self.settings.chain),
            created_at_ms=int(update.get("observed_at_ms") or 0),
            lot_id=lot_id,
            exit_fraction=Decimal("1"),
            reason="unexpected_open_exposure",
        )

    def health(self) -> Mapping[str, Any]:
        return {"engine_id": self.engine_id, "version": self.engine_version, "events": self._events, "signals": self._signals}


def build_engine(settings_path: str | Path, runtime_dir: str | Path) -> GPTNetEdgeArbEngine:
    return GPTNetEdgeArbEngine(load_settings(settings_path), runtime_dir)
