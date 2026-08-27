from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .contracts import MarketEvent, TradeIntent


def _d(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _b(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "pass", "ok"}


def _simple(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_simple(v) for v in value[:12]]
    if isinstance(value, Mapping):
        return {str(k): _simple(v) for k, v in list(value.items())[:40]}
    return str(value)


def _counter_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    old = before.get("prefilter_rejections") if isinstance(before, Mapping) else None
    new = after.get("prefilter_rejections") if isinstance(after, Mapping) else None
    if not isinstance(old, Mapping) or not isinstance(new, Mapping):
        return ""
    changed: list[tuple[int, str]] = []
    for key, value in new.items():
        try:
            delta = int(value or 0) - int(old.get(key, 0) or 0)
        except Exception:
            continue
        if delta > 0:
            changed.append((delta, str(key)))
    changed.sort(reverse=True)
    return changed[0][1] if changed else ""


def _gpt_reason(engine: Any, event: MarketEvent) -> str:
    settings = getattr(engine, "settings", None)
    payload = event.payload
    if event.event_type == "dex_spread":
        gross = _d(payload.get("gross_edge_bps"))
        costs = _d(payload.get("estimated_cost_bps"))
        if gross is None or costs is None:
            return "missing_edge_or_cost"
        age = int(payload.get("quote_age_ms") or 0)
        if age < 0 or age > int(getattr(settings, "max_quote_age_ms", 750)):
            return "stale_quote"
        buy = str(payload.get("buy_venue") or "").strip()
        sell = str(payload.get("sell_venue") or "").strip()
        if not buy or not sell:
            return "missing_cross_dex_venue"
        if buy == sell:
            return "same_venue_no_arbitrage"
        if gross - costs < Decimal(str(getattr(settings, "min_net_edge_bps", 12))):
            return "net_edge_floor"
        if not event.asset_in or not event.asset_out:
            return "missing_assets"
        return "no_trade_intent"

    if event.event_type == "evm_route":
        age = int(payload.get("quote_age_ms") or 0)
        if age < 0 or age > int(getattr(settings, "max_quote_age_ms", 750)):
            return "stale_quote"
        required = ("exact_quote_ok", "liquidity_ok", "route_approved", "whole_route_approved")
        missing = [key for key in required if not _b(payload.get(key))]
        if missing:
            return "route_preflight_" + "_".join(missing)
        route = tuple(str(x).strip() for x in (payload.get("route_path") or ()) if str(x).strip())
        if len(route) < 3 or route[0].lower() != route[-1].lower():
            return "invalid_atomic_cycle"
        gross = _d(payload.get("gross_edge_bps"))
        costs = _d(payload.get("estimated_cost_bps"))
        if gross is None or costs is None:
            return "missing_edge_or_cost"
        if gross - costs < Decimal(str(getattr(settings, "min_net_edge_bps", 12))):
            return "net_edge_floor"
        if not event.asset_in or not event.asset_out:
            return "missing_assets"
        return "no_trade_intent"
    return ""


def derive_market_rejection(engine_id: str, engine: Any, event: MarketEvent, before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    expected_chain = str(getattr(getattr(engine, "settings", None), "chain", "") or "").lower()
    if expected_chain and event.chain.lower() != expected_chain:
        return ""
    if not event.asset_out and not (engine_id == "gpt" and event.payload.get("route_path")):
        return ""
    reason = _counter_delta(before, after)
    if reason == "wrong_chain_or_assets":
        return ""
    if reason:
        return reason
    if engine_id == "gpt":
        return _gpt_reason(engine, event)
    return ""


def _event_token(engine_id: str, event: MarketEvent) -> str:
    if engine_id == "gpt":
        route = event.payload.get("route_path") or ()
        if isinstance(route, (list, tuple)) and len(route) >= 2:
            token = str(route[1] or "").strip()
            if token:
                return token
    return str(event.asset_out or "").strip()


def publish_market_rejection(engine_id: str, engine: Any, event: MarketEvent, reason: str) -> None:
    reason = str(reason or "").strip()
    token = _event_token(engine_id, event)
    if not reason or not token:
        return
    try:
        from learnerbot.rejected_opportunity_publisher import publish_rejection

        payload = {
            "risk_class": "STRATEGY_PREFILTER_REJECT",
            "source_runtime": "sibot1_strategy_worker",
            "event_type": event.event_type,
            "source": event.source,
            "price": _simple(event.price),
            "liquidity_usd": _simple(event.liquidity_usd),
            "volume_usd": _simple(event.volume_usd),
            "source_age_ms": _simple(event.source_age_ms),
        }
        for key in (
            "gross_edge_bps", "estimated_cost_bps", "quote_age_ms", "buy_venue", "sell_venue",
            "route_path", "venue_plan", "volume_velocity", "liquidity_velocity", "confidence",
            "dev_selling_known", "dev_selling", "lp_locked_pct", "mint_authority_present",
            "freeze_authority_present", "exact_quote_ok", "liquidity_ok", "route_approved",
            "whole_route_approved",
        ):
            if key in event.payload:
                payload[key] = _simple(event.payload.get(key))
        publish_rejection(
            chain=event.chain,
            token_address=token,
            pool_address=str(event.pool_id or ""),
            dex=str(event.payload.get("venue") or event.payload.get("buy_venue") or ""),
            source=engine_id,
            source_strategy_id=str(getattr(getattr(engine, "settings", None), "strategy_id", engine_id) or engine_id),
            source_event_id=str(event.event_id or ""),
            rejection_class="STRATEGY_PREFILTER_REJECT",
            rejection_reason=reason,
            priority=70 if engine_id in {"gemini", "grok"} else 55,
            observed_at=max(1, int(event.observed_at_ms or 0) // 1000),
            payload=payload,
            require_market_reason=False,
        )
    except Exception:
        return


def publish_intent_rejection(intent: TradeIntent, rejection_class: str, reason: str, *, payload: Mapping[str, Any] | None = None) -> None:
    token = str(intent.asset_out or "").strip()
    if not token or not reason:
        return
    if intent.engine_id == "gpt" and intent.route_hint and len(intent.route_hint) >= 2:
        candidate = str(intent.route_hint[1] or "").strip()
        if candidate.startswith("0x"):
            token = candidate
    try:
        from learnerbot.rejected_opportunity_publisher import publish_rejection

        evidence = {
            "risk_class": str(rejection_class or "STRATEGY_REJECT"),
            "source_runtime": "sibot1_central_runtime",
            "intent_id": intent.intent_id,
            "market_event_id": intent.market_event_id or "",
            **{str(k): _simple(v) for k, v in dict(payload or {}).items()},
        }
        publish_rejection(
            chain=intent.chain,
            token_address=token,
            source=intent.engine_id,
            source_strategy_id=intent.strategy_id,
            source_event_id=intent.market_event_id or intent.intent_id,
            rejection_class=str(rejection_class or "STRATEGY_REJECT"),
            rejection_reason=str(reason),
            priority=80 if intent.engine_id in {"gemini", "grok"} else 60,
            observed_at=max(1, int(intent.created_at_ms or 0) // 1000),
            payload=evidence,
            require_market_reason=False,
        )
    except Exception:
        return
