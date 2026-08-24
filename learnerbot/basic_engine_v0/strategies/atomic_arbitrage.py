from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping

from ..core import Candidate, Quote, RiskDecision


@dataclass(frozen=True)
class AtomicArbitrageRoute:
    """One atomic round-trip route proposed to the engine.

    The route never owns a wallet, signer, or broadcaster. Its path must start
    and end in the same asset so the strategy does not intentionally leave an
    open position behind.
    """

    route_id: str
    chain: str
    path: tuple[str, ...]
    input_value: Decimal
    priority: Decimal = Decimal("0")
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.route_id.strip():
            raise ValueError("route_id is required")
        if not self.chain.strip():
            raise ValueError("chain is required")
        if len(self.path) < 3:
            raise ValueError(
                "atomic arbitrage path requires at least 3 assets including the repeated start/end asset"
            )
        if str(self.path[0]).lower() != str(self.path[-1]).lower():
            raise ValueError("atomic arbitrage path must return to its starting asset")
        if self.input_value <= 0:
            raise ValueError("input_value must be greater than zero")

    def to_candidate(self) -> Candidate:
        self.validate()
        return Candidate(
            candidate_id=f"atomic:{self.chain}:{self.route_id}",
            chain=self.chain,
            strategy="atomic_arbitrage",
            priority=self.priority,
            payload={
                "route_id": self.route_id,
                "path": self.path,
                "starting_asset": self.path[0],
                "input_value": self.input_value,
                **dict(self.metadata),
            },
        )


class AtomicArbitrageSource:
    """Simple v0 route source.

    Later market scanners can replace this source without changing the engine
    or gaining signing authority.
    """

    def __init__(self, routes: Iterable[AtomicArbitrageRoute]) -> None:
        self._routes = tuple(routes)

    def scan(self) -> tuple[Candidate, ...]:
        return tuple(route.to_candidate() for route in self._routes)


@dataclass(frozen=True)
class AtomicArbitragePolicy:
    """Strategy economics applied before simulation and before final execution."""

    min_net_profit: Decimal = Decimal("0")
    safety_buffer: Decimal = Decimal("0")
    max_price_impact_bps: int = 500

    def __post_init__(self) -> None:
        if self.min_net_profit < 0:
            raise ValueError("min_net_profit cannot be negative")
        if self.safety_buffer < 0:
            raise ValueError("safety_buffer cannot be negative")
        if not 0 <= self.max_price_impact_bps <= 10_000:
            raise ValueError("max_price_impact_bps must be between 0 and 10000")


class AtomicArbitrageRiskGate:
    """Fail-closed round-trip and net-profit gate for atomic arbitrage.

    `Quote.expected_profit` is already net of `Quote.estimated_fees`. The
    configured safety buffer is reserved on top. Pool-rug, honeypot,
    quarantine, exposure, wallet and chain-health checks stay separate risk
    plugins so they can be added without rewriting this strategy.
    """

    name = "atomic_arbitrage"

    def __init__(self, policy: AtomicArbitragePolicy | None = None) -> None:
        self.policy = policy or AtomicArbitragePolicy()

    def check(self, candidate: Candidate, quote: Quote) -> RiskDecision:
        if candidate.strategy != "atomic_arbitrage":
            return RiskDecision(False, "WRONG_STRATEGY")

        path = tuple(candidate.payload.get("path") or ())
        if len(path) < 3:
            return RiskDecision(False, "INVALID_PATH")
        if str(path[0]).lower() != str(path[-1]).lower():
            return RiskDecision(False, "NOT_ATOMIC_ROUND_TRIP")

        try:
            configured_input = Decimal(str(candidate.payload.get("input_value")))
        except Exception:
            return RiskDecision(False, "INVALID_INPUT_VALUE")

        if configured_input <= 0 or quote.input_value <= 0:
            return RiskDecision(False, "INVALID_INPUT_VALUE")
        if quote.input_value != configured_input:
            return RiskDecision(False, "QUOTE_INPUT_MISMATCH")
        if not quote.executable:
            return RiskDecision(False, "QUOTE_NOT_EXECUTABLE")

        if (
            quote.price_impact_bps < 0
            or quote.price_impact_bps > self.policy.max_price_impact_bps
        ):
            return RiskDecision(False, "PRICE_IMPACT_TOO_HIGH")

        net_after_buffer = quote.expected_profit - self.policy.safety_buffer
        if net_after_buffer < self.policy.min_net_profit:
            return RiskDecision(
                False,
                "NET_PROFIT_BELOW_MINIMUM",
                metadata={
                    "quote_expected_profit": str(quote.expected_profit),
                    "safety_buffer": str(self.policy.safety_buffer),
                    "net_after_buffer": str(net_after_buffer),
                    "minimum": str(self.policy.min_net_profit),
                },
            )

        return RiskDecision(
            True,
            metadata={
                "quote_expected_profit": str(quote.expected_profit),
                "safety_buffer": str(self.policy.safety_buffer),
                "net_after_buffer": str(net_after_buffer),
            },
        )
