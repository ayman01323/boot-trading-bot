from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class Candidate:
    """A strategy-neutral opportunity proposed to the engine."""

    candidate_id: str
    chain: str
    strategy: str
    priority: Decimal = Decimal("0")
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Quote:
    """A fresh executable-market view for one candidate."""

    candidate_id: str
    executable: bool
    input_value: Decimal
    expected_output_value: Decimal
    estimated_fees: Decimal = Decimal("0")
    price_impact_bps: int = 0
    route_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def expected_profit(self) -> Decimal:
        return self.expected_output_value - self.input_value - self.estimated_fees


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str = "OK"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SimulationResult:
    ok: bool
    expected_profit: Decimal = Decimal("0")
    reason: str = "OK"
    transaction_preview: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    submitted: bool
    tx_id: str | None = None
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class DecisionStatus(str, Enum):
    NO_CANDIDATE = "NO_CANDIDATE"
    REJECTED = "REJECTED"
    DRY_RUN_READY = "DRY_RUN_READY"
    EXECUTED = "EXECUTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


@dataclass(frozen=True)
class EngineDecision:
    status: DecisionStatus
    candidate_id: str | None = None
    reason: str = ""
    quote: Quote | None = None
    simulation: SimulationResult | None = None
    execution: ExecutionResult | None = None


@dataclass(frozen=True)
class EngineConfig:
    """Core safety policy. Execution is deliberately disabled by default."""

    execution_enabled: bool = False
    min_expected_profit: Decimal = Decimal("0")
    max_candidates_per_cycle: int = 25
    require_same_route_on_recheck: bool = False


class CandidateSource(Protocol):
    def scan(self) -> Sequence[Candidate]: ...


class Quoter(Protocol):
    def quote(self, candidate: Candidate) -> Quote: ...


class RiskGate(Protocol):
    name: str

    def check(self, candidate: Candidate, quote: Quote) -> RiskDecision: ...


class Simulator(Protocol):
    def simulate(self, candidate: Candidate, quote: Quote) -> SimulationResult: ...


class Executor(Protocol):
    def execute(
        self,
        candidate: Candidate,
        quote: Quote,
        simulation: SimulationResult,
    ) -> ExecutionResult: ...


class EngineObserver(Protocol):
    def on_event(self, event: str, payload: Mapping[str, Any]) -> None: ...
