from __future__ import annotations

from collections import Counter
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from sibot1_engines._shared.contracts import ExitIntent, MarketEvent, TradeIntent
from sibot1_engines._shared.solana_dev_flow import SolanaDeveloperFlowResolver

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
    """Nominate a current same-executor EVM cycle for strict LIVE revalidation."""
    if event.event_type != "evm_route":
        return None
    payload = event.payload
    age = int(payload.get("quote_age_ms") or 0)
    if age < 0 or age > settings.max_quote_age_ms:
        return None

    required = (
        "exact_quote_ok",
        "liquidity_ok",
        "route_approved",
        "whole_route_approved",
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
    source_sim = _b(payload.get("simulation_ok"))
    source_atomic = _b(payload.get("atomic_profit_protection"))
    return {
        "gross_edge_bps": gross,
        "estimated_cost_bps": costs,
        "net_edge_bps": net,
        "route": route,
        "source_path": str(payload.get("source_path") or ""),
        "venue_plan": tuple(str(x) for x in (payload.get("venue_plan") or ())),
        "source_simulation_ok": source_sim,
        "source_atomic_profit_protection": source_atomic,
        "source_preflight_complete": bool(source_sim and source_atomic),
        "live_revalidation_required": True,
    }


class GPTNetEdgeArbEngine:
    """GPT dual-path engine: Base atomic edge + Solana leader-quality momentum.

    The Solana path intentionally does not require positive volume velocity.  The
    shared source starts each newly observed mint at zero velocity, which starves
    engines that demand a positive delta on the first sample.  GPT instead demands
    a recent leader BUY, materially stronger absolute liquidity/volume, acceptable
    liquidity trend, confidence from source completeness, and fail-closed developer
    evidence.  Central PoolCheck and the Solana LIVE bridge remain authoritative.
    """

    engine_id = "gpt"
    engine_version = "1.3.0"

    def __init__(
        self,
        settings: Settings,
        runtime_dir: str | Path,
        dev_flow_resolver: SolanaDeveloperFlowResolver | None = None,
    ):
        self.settings = settings
        self.runtime_dir = Path(runtime_dir)
        self.dev_flow = dev_flow_resolver or SolanaDeveloperFlowResolver()
        self._events = 0
        self._signals = 0
        self._cycle_signals = 0
        self._spread_signals = 0
        self._solana_signals = 0
        self._solana_dev_known_safe = 0
        self._solana_dev_selling = 0
        self._solana_dev_unknown = 0
        self._solana_rejections: Counter[str] = Counter()

    def _reject_solana(self, reason: str) -> None:
        self._solana_rejections[reason] += 1
        return None

    def _solana_precheck(self, event: MarketEvent) -> dict[str, Decimal | int] | None:
        if not self.settings.solana_enabled:
            return self._reject_solana("disabled")
        if event.chain.lower() != "solana" or event.event_type != "market_pulse":
            return self._reject_solana("wrong_chain_or_event")
        if not event.asset_out or event.price is None:
            return self._reject_solana("missing_asset_or_price")
        age = int(event.source_age_ms if event.source_age_ms is not None else 10**9)
        if age < 0 or age > self.settings.solana_max_source_age_ms:
            return self._reject_solana("stale_market_data")

        liquidity = _d(event.liquidity_usd)
        volume = _d(event.volume_usd)
        confidence = _d(event.payload.get("confidence"))
        leader_age = int(event.payload.get("leader_event_age_ms") or 10**9)
        liquidity_velocity = _d(event.payload.get("liquidity_velocity"))
        if liquidity is None or liquidity < self.settings.solana_min_liquidity_usd:
            return self._reject_solana("liquidity_floor")
        if volume is None or volume < self.settings.solana_min_volume_usd:
            return self._reject_solana("volume_floor")
        ratio = volume / max(Decimal("1"), liquidity)
        if ratio < self.settings.solana_min_volume_liquidity_ratio:
            return self._reject_solana("volume_liquidity_ratio_floor")
        if confidence is None or confidence < self.settings.solana_min_confidence:
            return self._reject_solana("confidence_floor")
        if leader_age < 0 or leader_age > self.settings.solana_max_leader_age_ms:
            return self._reject_solana("leader_age_floor")
        if liquidity_velocity is None:
            return self._reject_solana("liquidity_velocity_missing")
        if liquidity_velocity < self.settings.solana_min_liquidity_velocity_pct:
            return self._reject_solana("liquidity_velocity_floor")
        return {
            "liquidity": liquidity,
            "volume": volume,
            "ratio": ratio,
            "confidence": confidence,
            "leader_age": leader_age,
            "liquidity_velocity": liquidity_velocity,
        }

    def _solana_intent(self, event: MarketEvent) -> TradeIntent | None:
        quality = self._solana_precheck(event)
        if quality is None:
            return None

        enriched = event
        if self.settings.solana_require_dev_safe and not bool(event.payload.get("dev_selling_known", False)):
            evidence = self.dev_flow.resolve(str(event.asset_out))
            enriched = replace(event, payload={**dict(event.payload), **evidence.as_payload()})
            if evidence.known and evidence.selling:
                self._solana_dev_selling += 1
            elif evidence.known:
                self._solana_dev_known_safe += 1
            else:
                self._solana_dev_unknown += 1

        if self.settings.solana_require_dev_safe:
            if not bool(enriched.payload.get("dev_selling_known", False)):
                return self._reject_solana("developer_flow_unknown")
            if bool(enriched.payload.get("dev_selling", False)):
                return self._reject_solana("developer_selling")

        if enriched.payload.get("mint_authority_present") is True:
            return self._reject_solana("mint_authority_present")
        if enriched.payload.get("freeze_authority_present") is True:
            return self._reject_solana("freeze_authority_present")
        lp_locked = _d(enriched.payload.get("lp_locked_pct"))
        if lp_locked is not None and lp_locked < self.settings.solana_min_lp_locked_pct:
            return self._reject_solana("lp_locked_floor")

        self._signals += 1
        self._solana_signals += 1
        return TradeIntent(
            intent_id=f"gpt-sol-{uuid4().hex}",
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            strategy_id=self.settings.solana_strategy_id,
            chain="solana",
            side="BUY",
            asset_in="SOL",
            asset_out=str(enriched.asset_out),
            requested_input_amount=self.settings.solana_trade_size_quote,
            created_at_ms=enriched.observed_at_ms,
            venue=str(enriched.payload.get("venue") or "") or None,
            confidence=quality["confidence"],
            signal_id=f"leader-quality:{enriched.event_id}",
            market_event_id=enriched.event_id,
            metadata={
                "execution_family": "SOLANA_LEADER_QUALITY",
                "leader_event_age_ms": int(quality["leader_age"]),
                "liquidity_usd": str(quality["liquidity"]),
                "volume_usd": str(quality["volume"]),
                "volume_liquidity_ratio": str(quality["ratio"]),
                "liquidity_velocity": str(quality["liquidity_velocity"]),
                "dev_selling_known": bool(enriched.payload.get("dev_selling_known", False)),
                "dev_selling": bool(enriched.payload.get("dev_selling", False)),
                "dev_selling_source": str(enriched.payload.get("dev_selling_source") or ""),
                "lp_locked_pct": str(enriched.payload.get("lp_locked_pct") or ""),
                "live_revalidation_required": True,
            },
        )

    def on_market_event(self, event: MarketEvent) -> TradeIntent | None:
        self._events += 1

        if event.chain.lower() == "solana":
            return self._solana_intent(event)
        if event.chain.lower() != self.settings.chain.lower():
            return None

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
                "live_revalidation_required": True,
                "source_simulation_ok": cycle["source_simulation_ok"],
                "source_atomic_profit_protection": cycle["source_atomic_profit_protection"],
                "source_preflight_complete": cycle["source_preflight_complete"],
            },
        )

    def on_position_update(self, update: Mapping[str, Any]) -> ExitIntent | None:
        if str(update.get("engine_id") or "") != self.engine_id:
            return None
        lot_id = str(update.get("lot_id") or "").strip()
        if not lot_id:
            return None
        chain = str(update.get("chain") or self.settings.chain).lower()
        age_ms = int(update.get("age_ms") or 0)
        pnl_pct = Decimal(str(update.get("pnl_pct") or "0"))

        if chain == "solana":
            reason = ""
            if pnl_pct >= self.settings.solana_take_profit_pct:
                reason = "take_profit"
            elif pnl_pct <= self.settings.solana_stop_loss_pct:
                reason = "stop_loss"
            elif age_ms >= self.settings.solana_max_open_ms:
                reason = "time_stop"
            if not reason:
                return None
            return ExitIntent(
                intent_id=f"gpt-sol-exit-{uuid4().hex}",
                engine_id=self.engine_id,
                engine_version=self.engine_version,
                strategy_id=self.settings.solana_strategy_id,
                chain="solana",
                created_at_ms=int(update.get("observed_at_ms") or 0),
                lot_id=lot_id,
                exit_fraction=Decimal("1"),
                reason=reason,
            )

        if age_ms < self.settings.max_open_ms and pnl_pct > -self.settings.emergency_loss_pct:
            return None
        return ExitIntent(
            intent_id=f"gpt-exit-{uuid4().hex}",
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            strategy_id=self.settings.strategy_id,
            chain=chain,
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
            "solana_signals": self._solana_signals,
            "developer_flow_known_safe": self._solana_dev_known_safe,
            "developer_flow_selling": self._solana_dev_selling,
            "developer_flow_unknown": self._solana_dev_unknown,
            "prefilter_rejections": dict(self._solana_rejections),
        }


def build_engine(settings_path: str | Path, runtime_dir: str | Path) -> GPTNetEdgeArbEngine:
    return GPTNetEdgeArbEngine(load_settings(settings_path), runtime_dir)
