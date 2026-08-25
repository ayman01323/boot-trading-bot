from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from .contracts import ExitIntent, MarketEvent, TradeIntent
from .ports import ExecutionReceipt


@dataclass(frozen=True, slots=True)
class PricePoint:
    chain: str
    asset: str
    quote_asset: str
    price: Decimal
    observed_at_ms: int
    event_id: str


class MarketPriceBook:
    def __init__(self):
        self._by_event: dict[str, PricePoint] = {}
        self._latest: dict[tuple[str, str], PricePoint] = {}
        self._lock = RLock()

    def observe(self, event: MarketEvent) -> None:
        if event.price is None or event.price <= 0 or not event.asset_out:
            return
        point = PricePoint(
            chain=str(event.chain).lower(),
            asset=str(event.asset_out),
            quote_asset=str(event.asset_in or "QUOTE"),
            price=Decimal(event.price),
            observed_at_ms=int(event.observed_at_ms),
            event_id=event.event_id,
        )
        with self._lock:
            self._by_event[event.event_id] = point
            self._latest[(point.chain, point.asset)] = point

    def for_event(self, event_id: str | None) -> PricePoint | None:
        if not event_id:
            return None
        with self._lock:
            return self._by_event.get(str(event_id))

    def latest(self, chain: str, asset: str) -> PricePoint | None:
        with self._lock:
            return self._latest.get((str(chain).lower(), str(asset)))


class ShadowPaperExecutor:
    """Shared execution port that can never sign or broadcast.

    There is intentionally no private-key parameter, signer method, RPC send
    method, transaction builder or LIVE mode. The constructor rejects every mode
    except SHADOW/PAPER. Real execution requires a separate future implementation
    and operator-controlled deployment path.
    """

    def __init__(self, prices: MarketPriceBook, mode: str = "SHADOW"):
        mode = str(mode).upper()
        if mode not in {"SHADOW", "PAPER"}:
            raise RuntimeError("SiBot1 ShadowPaperExecutor refuses LIVE execution")
        self.mode = mode
        self.prices = prices

    @staticmethod
    def _tx(prefix: str) -> str:
        return f"{prefix}-{int(time.time() * 1000)}-{uuid4().hex[:12]}"

    def execute_entry(self, intent: TradeIntent, reservation_id: str) -> ExecutionReceipt:
        amount = Decimal(intent.requested_input_amount)
        if intent.side.upper() == "ARBITRAGE":
            expected = max(Decimal("0"), Decimal(intent.expected_net_profit or 0))
            return ExecutionReceipt(
                tx_id=self._tx("paper-arb"),
                engine_id=intent.engine_id,
                chain=intent.chain,
                status="PAPER_ATOMIC_ESTIMATE",
                actual_input_cost=amount,
                proceeds=amount + expected,
                metadata={
                    "mode": self.mode,
                    "reservation_id": reservation_id,
                    "basis": "expected_net_profit_estimate_not_live_fill",
                    "broadcast": False,
                },
            )
        point = self.prices.for_event(intent.market_event_id)
        if point is None or point.price <= 0:
            return ExecutionReceipt(
                tx_id=self._tx("paper-reject"),
                engine_id=intent.engine_id,
                chain=intent.chain,
                status="PAPER_REJECTED_NO_PRICE",
                metadata={"mode": self.mode, "reservation_id": reservation_id, "broadcast": False},
            )
        quantity = amount / point.price
        return ExecutionReceipt(
            tx_id=self._tx("paper-buy"),
            engine_id=intent.engine_id,
            chain=intent.chain,
            status="PAPER_FILLED",
            actual_input_cost=amount,
            acquired_asset=intent.asset_out,
            acquired_quantity=quantity,
            metadata={
                "mode": self.mode,
                "reservation_id": reservation_id,
                "paper_price": str(point.price),
                "quote_asset": point.quote_asset,
                "market_event_id": point.event_id,
                "broadcast": False,
            },
        )

    def execute_exit(self, intent: ExitIntent, *, quantity: Decimal) -> ExecutionReceipt:
        asset = str(intent.asset or "").strip()
        if not asset:
            return ExecutionReceipt(
                tx_id=self._tx("paper-reject"),
                engine_id=intent.engine_id,
                chain=intent.chain,
                status="PAPER_REJECTED_NO_ASSET",
                metadata={"mode": self.mode, "broadcast": False},
            )
        point = self.prices.latest(intent.chain, asset)
        if point is None or point.price <= 0:
            return ExecutionReceipt(
                tx_id=self._tx("paper-reject"),
                engine_id=intent.engine_id,
                chain=intent.chain,
                status="PAPER_REJECTED_NO_PRICE",
                metadata={"mode": self.mode, "asset": asset, "broadcast": False},
            )
        qty = Decimal(quantity)
        return ExecutionReceipt(
            tx_id=self._tx("paper-sell"),
            engine_id=intent.engine_id,
            chain=intent.chain,
            status="PAPER_FILLED",
            proceeds=qty * point.price,
            metadata={
                "mode": self.mode,
                "paper_price": str(point.price),
                "quote_asset": point.quote_asset,
                "market_event_id": point.event_id,
                "broadcast": False,
            },
        )
