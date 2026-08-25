from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Protocol

from .contracts import ExitIntent, TradeIntent


@dataclass(frozen=True, slots=True)
class PoolCheckDecision:
    verdict: str
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict.upper() == "PASS"


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    tx_id: str
    engine_id: str
    chain: str
    status: str
    actual_input_cost: Decimal = Decimal("0")
    acquired_asset: str | None = None
    acquired_quantity: Decimal = Decimal("0")
    proceeds: Decimal = Decimal("0")
    metadata: Mapping[str, Any] = field(default_factory=dict)


class PoolCheckPort(Protocol):
    def assess_entry(self, intent: TradeIntent) -> PoolCheckDecision: ...
    def assess_open_position(self, *, chain: str, asset: str) -> PoolCheckDecision: ...


class ExecutionPort(Protocol):
    """Only shared infrastructure may implement this port."""
    def execute_entry(self, intent: TradeIntent, reservation_id: str) -> ExecutionReceipt: ...
    def execute_exit(self, intent: ExitIntent, *, quantity: Decimal) -> ExecutionReceipt: ...
