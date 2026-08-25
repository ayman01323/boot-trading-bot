from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from sibot1_engines._shared.contracts import ExitIntent, MarketEvent, TradeIntent

from .settings_schema import Settings, load_settings
from .strategy import score_spread


def _d(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _b(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "pass", "ok"}


def _score_atomic_cycle(event: MarketEvent, settings: Settings) -> dict[str, Any] | None:
    """Score a pre-approved same-executor EVM cycle for SiBot 1.

    This is deliberately narrower than the GPT cross-DEX spread strategy.  It
    accepts only an upstream route that already carries the exact quote,
    simulation, liquidity, whole-route and atomic profit-protection evidence.
    The LIVE bridge still performs its own wallet-specific quote, simulation and
    pre-broadcast eth_call immediately before signing.
    """
    if event.event_type != "evm_route":
        return None
    payload = event.payload
    age = int(payload.get("quote_age_ms") or 0)
    if age < 0 or age > settings.max_quote_age_ms:
        return None
    required = (
        "exact_quote_ok",
        "simulation_ok",
        "liquidity_ok",
        "route_approved",
        "whole_route_approved",
        "atomic_profit_protection",
    )
    if not all(_b(payload.get(key)) for key in required):
        return None
    route = tuple(str(x).strip() for x in (payload.get("route_path") or ()) if str(x).strip())
    if len(route) < 3 or route[0].lower() != route[-1].lower():
        return None
    gross = _d(payload.get("gross_edge_bps"))
    costs = _d(payload.get("estimated_cost_bps"))
    if gross is None or costs is None:
        return None
    net = gross - costs
    if net < settings.min_net_edge_bps:
        return None
    return {
        "gross_edge_bps": gross,
        "estimated_cost_bps": costs,
        "net_edge_bps": net,
        "route": route,
        "source_path": str(payload.get("source_path") or ""),
        "venue_plan": tuple(str(x) for x in (payload.get("venue_plan") or ())),
    }


class GPTNetEdgeArbEngine:
    engine_id = "gpt"
    engine_version = "1.1.0"

    def __init__(self, settings: Settings, runtime_dir: str | Path):
        self.settings = settings
        self.runtime_dir = Path(runtime_dir)
        self._events = 0
        self._signals = 0
        self._cycle_signals = 0
        self._spread_signals = 0

    def on_market_event(self, event: MarketEvent) -> TradeIntent | None:
        self._events += 1
        if event.chain.lower() != self.settings.chain.lower():
            return None

        # Existing GPT cross-DEX research path.  These intents remain paper-only
        # unless a genuinely atomic multi-venue executor is available.
        if event.event_type == "dex_spread":
            score = score_spread(event.payload, self.settings)
            if score is None:
                return None
            asset_in = str(event.asset_in or event.payload.get("asset_in") or "").strip()
            asset_out = str(event.asset_out or event.payload.get("asset_out") or "").strip()
            if not asset_in or not asset_out:
                return None
            self._signals += 1
            self._spread_signals += 1
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
                    "execution_family": "CROSS_DEX_RESEARCH",
                    "gross_edge_bps": str(score["gross_edge_bps"]),
                    "estimated_cost_bps": str(score["estimated_cost_bps"]),
                    "net_edge_bps": str(score["net_edge_bps"]),
                    "atomic_required": True,
                },
            )

        # New LIVE-capable path: only upstream-approved single-executor atomic
        # cycles.  The engine still never signs; it merely emits an intent.
        cycle = _score_atomic_cycle(event, self.settings)
        if cycle is None:
            return None
        asset_in = str(event.asset_in or "").strip()
        asset_out = str(event.asset_out or "").strip()
        if not asset_in or not asset_out:
            return None
        self._signals += 1
        self._cycle_signals += 1
        requested = self.settings.trade_size_quote
        expected_net = requested * Decimal(cycle["net_edge_bps"]) / Decimal("10000")
        return TradeIntent(
            intent_id=f"gpt-cycle-{uuid4().hex}",
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            strategy_id=self.settings.strategy_id,
            chain=event.chain,
            side="ARBITRAGE",
            asset_in=asset_in,
            asset_out=asset_out,
            requested_input_amount=requested,
            created_at_ms=event.observed_at_ms,
            venue="atomic-cycle",
            route_hint=cycle["route"],
            expected_net_profit=expected_net,
            signal_id=f"cycle:{event.event_id}",
            market_event_id=event.event_id,
            metadata={
                "execution_family": "ATOMIC_CYCLE",
                "route_path": list(cycle["route"]),
                "source_path": cycle["source_path"],
                "venue_plan": list(cycle["venue_plan"]),
                "gross_edge_bps": str(cycle["gross_edge_bps"]),
                "estimated_cost_bps": str(cycle["estimated_cost_bps"]),
                "net_edge_bps": str(cycle["net_edge_bps"]),
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
        return {
            "engine_id": self.engine_id,
            "version": self.engine_version,
            "events": self._events,
            "signals": self._signals,
            "spread_signals": self._spread_signals,
            "cycle_signals": self._cycle_signals,
        }


def build_engine(settings_path: str | Path, runtime_dir: str | Path) -> GPTNetEdgeArbEngine:
    return GPTNetEdgeArbEngine(load_settings(settings_path), runtime_dir)
