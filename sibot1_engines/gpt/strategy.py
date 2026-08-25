from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .settings_schema import Settings


def _d(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def score_spread(payload: Mapping[str, Any], settings: Settings) -> dict[str, Any] | None:
    gross = _d(payload.get("gross_edge_bps"))
    costs = _d(payload.get("estimated_cost_bps"))
    if gross is None or costs is None:
        return None
    age = int(payload.get("quote_age_ms") or 0)
    if age < 0 or age > settings.max_quote_age_ms:
        return None
    buy_venue = str(payload.get("buy_venue") or "").strip()
    sell_venue = str(payload.get("sell_venue") or "").strip()
    if not buy_venue or not sell_venue or buy_venue == sell_venue:
        return None
    net = gross - costs
    if net < settings.min_net_edge_bps:
        return None
    return {
        "gross_edge_bps": gross,
        "estimated_cost_bps": costs,
        "net_edge_bps": net,
        "buy_venue": buy_venue,
        "sell_venue": sell_venue,
    }
