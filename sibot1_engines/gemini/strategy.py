from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sibot1_engines._shared.contracts import MarketEvent

from .settings_schema import Settings


class PulseFlowStrategy:
    """Gemini-authored liquidity/volume pulse gate, integrated to SiBot 1 v1.

    The prefilter is deliberately cheap and never replaces central PoolCheck.
    It rejects obviously poor/repeated candidates before they consume another
    full safety evaluation. Unknown authority/LP evidence is left for PoolCheck
    rather than being silently treated as safe.
    """

    def __init__(self, settings: Settings):
        self.s = settings
        self._last_signal_at_ms: dict[str, int] = {}
        self._rejections: Counter[str] = Counter()

    def _reject(self, reason: str):
        self._rejections[reason] += 1
        return None

    @staticmethod
    def _optional_decimal(value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def rejection_counts(self) -> dict[str, int]:
        return dict(self._rejections)

    def entry_signal(self, event: MarketEvent) -> tuple[str, str, Decimal, Decimal] | None:
        if event.chain.lower() != self.s.chain or not event.asset_in or not event.asset_out:
            return self._reject("wrong_chain_or_assets")
        if event.price is None or event.liquidity_usd is None or event.volume_usd is None:
            return self._reject("missing_market_data")
        if event.source_age_ms is None or event.source_age_ms < 0 or event.source_age_ms > self.s.max_source_age_ms:
            return self._reject("stale_market_data")

        liq = Decimal(event.liquidity_usd)
        vol = Decimal(event.volume_usd)
        if liq < self.s.min_liquidity_usd:
            return self._reject("liquidity_floor")
        if vol < self.s.min_volume_usd:
            return self._reject("volume_floor")

        # Cheap wash/dead-pool discriminator using fields already present on the
        # shared market event; no provider call is needed here.
        ratio = vol / max(Decimal("1"), liq)
        if ratio < self.s.min_volume_liquidity_ratio:
            return self._reject("volume_liquidity_ratio_low")
        if ratio > self.s.max_volume_liquidity_ratio:
            return self._reject("volume_liquidity_ratio_high")

        lv = Decimal(str(event.payload.get("liquidity_velocity", "0")))
        if lv < self.s.min_liquidity_velocity_pct:
            return self._reject("liquidity_velocity_collapse")

        # If structural RugCheck evidence is already attached upstream, use it
        # as a cheap reject. Missing evidence is *not* promoted to safe; central
        # PoolCheck remains mandatory and fail-closed afterwards.
        if event.payload.get("mint_authority_present") is True:
            return self._reject("mint_authority_active")
        if event.payload.get("freeze_authority_present") is True:
            return self._reject("freeze_authority_active")
        lp_locked = self._optional_decimal(event.payload.get("lp_locked_pct"))
        if lp_locked is not None and lp_locked < self.s.min_lp_locked_pct_prefilter:
            return self._reject("lp_lock_prefilter")

        # Prevent the same mint from repeatedly generating a new intent on each
        # market pulse. PoolCheck remains authoritative on the first candidate.
        mint = str(event.asset_out)
        last = self._last_signal_at_ms.get(mint)
        if last is not None and max(0, int(event.observed_at_ms) - last) < self.s.signal_cooldown_ms:
            return self._reject("signal_cooldown")

        vv = Decimal(str(event.payload.get("volume_velocity", "0")))
        confidence = min(
            Decimal("0.99"),
            Decimal("0.70") + max(vv, Decimal("0")) / Decimal("10") + max(lv, Decimal("0")) / Decimal("10"),
        )
        self._last_signal_at_ms[mint] = int(event.observed_at_ms)
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
