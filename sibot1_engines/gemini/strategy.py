from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from sibot1_engines._shared.contracts import MarketEvent

from .settings_schema import Settings


class PulseFlowStrategy:
    """Gemini-authored liquidity/volume pulse gate, integrated to SiBot 1 v1."""

    def __init__(self, settings: Settings):
        self.s = settings

    def entry_signal(self, event: MarketEvent) -> tuple[str, str, Decimal, Decimal] | None:
        if event.chain.lower() != self.s.chain or not event.asset_in or not event.asset_out:
            return None
        if event.price is None or event.liquidity_usd is None or event.volume_usd is None:
            return None
        if event.source_age_ms is None or event.source_age_ms < 0 or event.source_age_ms > self.s.max_source_age_ms:
            return None
        liq = Decimal(event.liquidity_usd)
        vol = Decimal(event.volume_usd)
        if liq < self.s.min_liquidity_usd or vol < self.s.min_volume_usd:
            return None
        # Optional normalized hub velocity fields sharpen the pulse without making them mandatory.
        vv = Decimal(str(event.payload.get("volume_velocity", "0")))
        lv = Decimal(str(event.payload.get("liquidity_velocity", "0")))
        confidence = min(Decimal("0.99"), Decimal("0.70") + max(vv, Decimal("0")) / Decimal("10") + max(lv, Decimal("0")) / Decimal("10"))
        return event.asset_in, event.asset_out, self.s.trade_amount, confidence

    def exit_signal(self, update: Mapping[str, Any]) -> tuple[str, str | None, Decimal, str] | None:
        if str(update.get("engine_id") or "") != self.s.engine_id:
            return None
        lot_id = str(update.get("lot_id") or "").strip()
        if not lot_id:
            return None
        pnl_pct = Decimal(str(update.get("pnl_pct") or "0"))
        if pnl_pct >= self.s.take_profit_pct:
            return lot_id, str(update.get("asset") or "") or None, Decimal("1"), "take_profit"
        return None
