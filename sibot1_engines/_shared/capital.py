from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from threading import RLock
from uuid import uuid4


@dataclass(slots=True)
class EngineCapital:
    engine_id: str
    cash: Decimal
    reserved: Decimal = Decimal("0")
    invested_cost: Decimal = Decimal("0")
    realised_pnl: Decimal = Decimal("0")

    @property
    def equity_book(self) -> Decimal:
        return self.cash + self.reserved + self.invested_cost + self.realised_pnl


@dataclass(slots=True)
class Reservation:
    reservation_id: str
    engine_id: str
    amount: Decimal
    status: str = "RESERVED"


class CapitalManager:
    """Virtual sub-accounts over one physical wallet.

    This class never reads a wallet and never signs. It is a deterministic book/lock
    used before shared execution so engines cannot double-spend the same allocation.
    """

    def __init__(self, physical_budget: Decimal, allocations: dict[str, Decimal]):
        self.physical_budget = Decimal(physical_budget)
        if self.physical_budget < 0:
            raise ValueError("physical_budget must be non-negative")
        total = sum((Decimal(v) for v in allocations.values()), Decimal("0"))
        if total > self.physical_budget:
            raise ValueError("virtual allocations exceed physical budget")
        self._accounts = {k: EngineCapital(k, Decimal(v)) for k, v in allocations.items()}
        self._reservations: dict[str, Reservation] = {}
        self._lock = RLock()

    def snapshot(self, engine_id: str) -> EngineCapital:
        with self._lock:
            a = self._accounts[engine_id]
            return EngineCapital(a.engine_id, a.cash, a.reserved, a.invested_cost, a.realised_pnl)

    def reserve(self, engine_id: str, amount: Decimal) -> Reservation:
        amount = Decimal(amount)
        if amount <= 0:
            raise ValueError("amount must be positive")
        with self._lock:
            account = self._accounts[engine_id]
            if amount > account.cash:
                raise ValueError("insufficient virtual cash")
            account.cash -= amount
            account.reserved += amount
            r = Reservation(uuid4().hex, engine_id, amount)
            self._reservations[r.reservation_id] = r
            return Reservation(r.reservation_id, r.engine_id, r.amount, r.status)

    def release(self, reservation_id: str) -> None:
        with self._lock:
            r = self._reservations[reservation_id]
            if r.status != "RESERVED":
                raise ValueError("reservation is not releasable")
            a = self._accounts[r.engine_id]
            a.reserved -= r.amount
            a.cash += r.amount
            r.status = "RELEASED"

    def commit_entry(self, reservation_id: str, actual_cost: Decimal) -> Decimal:
        actual_cost = Decimal(actual_cost)
        with self._lock:
            r = self._reservations[reservation_id]
            if r.status != "RESERVED":
                raise ValueError("reservation is not active")
            if actual_cost < 0 or actual_cost > r.amount:
                raise ValueError("actual_cost exceeds reservation")
            a = self._accounts[r.engine_id]
            a.reserved -= r.amount
            a.invested_cost += actual_cost
            a.cash += r.amount - actual_cost
            r.status = "COMMITTED"
            return actual_cost

    def settle_exit(self, engine_id: str, cost_basis_released: Decimal, proceeds: Decimal) -> Decimal:
        cost = Decimal(cost_basis_released)
        proceeds = Decimal(proceeds)
        if cost < 0 or proceeds < 0:
            raise ValueError("settlement values must be non-negative")
        with self._lock:
            a = self._accounts[engine_id]
            if cost > a.invested_cost:
                raise ValueError("cost basis exceeds engine invested book")
            pnl = proceeds - cost
            a.invested_cost -= cost
            a.cash += proceeds
            a.realised_pnl += pnl
            return pnl
