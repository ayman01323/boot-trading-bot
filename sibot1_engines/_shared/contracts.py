"""Stable v1 contracts between independent SiBot 1 engines and shared infrastructure."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_id: str
    chain: str
    observed_at_ms: int
    source: str
    event_type: str
    asset_in: str | None = None
    asset_out: str | None = None
    pool_id: str | None = None
    price: Decimal | None = None
    liquidity_usd: Decimal | None = None
    volume_usd: Decimal | None = None
    source_age_ms: int | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TradeIntent:
    intent_id: str
    engine_id: str
    engine_version: str
    strategy_id: str
    chain: str
    side: str
    asset_in: str
    asset_out: str
    requested_input_amount: Decimal
    created_at_ms: int
    venue: str | None = None
    route_hint: Sequence[str] = field(default_factory=tuple)
    expected_gross_profit: Decimal | None = None
    expected_net_profit: Decimal | None = None
    confidence: Decimal | None = None
    signal_id: str | None = None
    market_event_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.requested_input_amount <= 0:
            raise ValueError("requested_input_amount must be positive")
        if self.side.upper() not in {"BUY", "ENTER", "ARBITRAGE"}:
            raise ValueError("TradeIntent side must be BUY, ENTER or ARBITRAGE")
        if not self.engine_id.strip():
            raise ValueError("engine_id is required")


@dataclass(frozen=True, slots=True)
class ExitIntent:
    intent_id: str
    engine_id: str
    engine_version: str
    strategy_id: str
    chain: str
    created_at_ms: int
    lot_id: str | None = None
    asset: str | None = None
    requested_quantity: Decimal | None = None
    exit_fraction: Decimal | None = None
    reason: str = "strategy_exit"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.engine_id.strip():
            raise ValueError("engine_id is required")
        if self.lot_id is None and self.asset is None:
            raise ValueError("ExitIntent requires lot_id or asset")
        if self.requested_quantity is not None and self.requested_quantity <= 0:
            raise ValueError("requested_quantity must be positive")
        if self.exit_fraction is not None and not (Decimal("0") < self.exit_fraction <= Decimal("1")):
            raise ValueError("exit_fraction must be in (0, 1]")


EngineIntent = TradeIntent | ExitIntent


@runtime_checkable
class SiBot1Engine(Protocol):
    """Interface every independent engine must implement.

    Implementations must be pure strategy/advisory code with respect to wallet
    execution: they may return intents but may not sign or broadcast transactions.
    """

    engine_id: str
    engine_version: str

    def on_market_event(self, event: MarketEvent) -> EngineIntent | Sequence[EngineIntent] | None:
        ...

    def on_position_update(self, update: Mapping[str, Any]) -> ExitIntent | Sequence[ExitIntent] | None:
        ...

    def health(self) -> Mapping[str, Any]:
        ...
