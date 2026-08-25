from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from threading import RLock
from uuid import uuid4


@dataclass(slots=True)
class PositionLot:
    lot_id: str
    engine_id: str
    engine_version: str
    strategy_id: str
    chain: str
    asset: str
    quantity: Decimal
    remaining_quantity: Decimal
    cost_basis: Decimal
    remaining_cost_basis: Decimal
    entry_tx: str
    entry_at_ms: int


@dataclass(frozen=True, slots=True)
class ExitSlice:
    lot_id: str
    engine_id: str
    quantity: Decimal
    cost_basis: Decimal


class PositionManager:
    """Attributes physical wallet token balances to engine-owned virtual lots."""

    def __init__(self):
        self._lots: dict[str, PositionLot] = {}
        self._lock = RLock()

    def open_lot(self, *, engine_id: str, engine_version: str, strategy_id: str, chain: str,
                 asset: str, quantity: Decimal, cost_basis: Decimal, entry_tx: str,
                 entry_at_ms: int) -> PositionLot:
        q, c = Decimal(quantity), Decimal(cost_basis)
        if q <= 0 or c < 0:
            raise ValueError("invalid lot quantity/cost")
        lot = PositionLot(uuid4().hex, engine_id, engine_version, strategy_id, chain, asset,
                          q, q, c, c, entry_tx, int(entry_at_ms))
        with self._lock:
            self._lots[lot.lot_id] = lot
        return lot

    def get(self, lot_id: str) -> PositionLot:
        with self._lock:
            return self._lots[lot_id]

    def plan_exit(self, *, engine_id: str, lot_id: str, quantity: Decimal | None = None,
                  fraction: Decimal | None = None) -> ExitSlice:
        with self._lock:
            lot = self._lots[lot_id]
            if lot.engine_id != engine_id:
                raise PermissionError("engine does not own lot")
            if lot.remaining_quantity <= 0:
                raise ValueError("lot is already closed")
            if quantity is not None and fraction is not None:
                raise ValueError("use quantity or fraction, not both")
            if fraction is not None:
                f = Decimal(fraction)
                if not (Decimal("0") < f <= Decimal("1")):
                    raise ValueError("fraction must be in (0,1]")
                q = lot.remaining_quantity * f
            elif quantity is not None:
                q = Decimal(quantity)
            else:
                q = lot.remaining_quantity
            if q <= 0 or q > lot.remaining_quantity:
                raise ValueError("exit exceeds owned remaining quantity")
            cost = lot.remaining_cost_basis * q / lot.remaining_quantity
            return ExitSlice(lot.lot_id, lot.engine_id, q, cost)

    def apply_exit(self, exit_slice: ExitSlice) -> None:
        with self._lock:
            lot = self._lots[exit_slice.lot_id]
            if lot.engine_id != exit_slice.engine_id:
                raise PermissionError("exit ownership mismatch")
            if exit_slice.quantity > lot.remaining_quantity:
                raise ValueError("exit exceeds remaining lot")
            lot.remaining_quantity -= exit_slice.quantity
            lot.remaining_cost_basis -= exit_slice.cost_basis
            if lot.remaining_quantity == 0:
                lot.remaining_cost_basis = Decimal("0")

    def emergency_slices(self, *, chain: str, asset: str) -> tuple[ExitSlice, ...]:
        """Safety-only selection across owners; settlement still preserves attribution."""
        with self._lock:
            rows = []
            for lot in self._lots.values():
                if lot.chain == chain and lot.asset == asset and lot.remaining_quantity > 0:
                    rows.append(ExitSlice(lot.lot_id, lot.engine_id, lot.remaining_quantity, lot.remaining_cost_basis))
            return tuple(rows)
