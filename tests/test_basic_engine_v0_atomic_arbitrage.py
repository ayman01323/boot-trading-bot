from decimal import Decimal

import pytest

from learnerbot.basic_engine_v0 import (
    BasicTradingEngine,
    EngineConfig,
    ExecutionResult,
    Quote,
    SimulationResult,
)
from learnerbot.basic_engine_v0.core import DecisionStatus
from learnerbot.basic_engine_v0.strategies import (
    AtomicArbitragePolicy,
    AtomicArbitrageRiskGate,
    AtomicArbitrageRoute,
    AtomicArbitrageSource,
)


def _route():
    return AtomicArbitrageRoute(
        route_id="base-usdc-cycle",
        chain="base",
        path=("USDC", "WETH", "TOKEN", "USDC"),
        input_value=Decimal("100"),
        priority=Decimal("10"),
    )


def test_source_builds_round_trip_candidate():
    candidate = AtomicArbitrageSource([_route()]).scan()[0]
    assert candidate.strategy == "atomic_arbitrage"
    assert candidate.payload["starting_asset"] == "USDC"
    assert candidate.payload["path"][-1] == "USDC"
    assert candidate.payload["input_value"] == Decimal("100")


def test_non_round_trip_route_is_rejected_at_source():
    route = AtomicArbitrageRoute(
        route_id="bad",
        chain="base",
        path=("USDC", "WETH", "TOKEN"),
        input_value=Decimal("100"),
    )
    with pytest.raises(ValueError, match="starting asset"):
        route.to_candidate()


def test_profit_gate_requires_profit_after_fee_and_buffer():
    candidate = _route().to_candidate()
    quote = Quote(
        candidate_id=candidate.candidate_id,
        executable=True,
        input_value=Decimal("100"),
        expected_output_value=Decimal("101.00"),
        estimated_fees=Decimal("0.30"),
        price_impact_bps=40,
    )
    decision = AtomicArbitrageRiskGate(
        AtomicArbitragePolicy(
            min_net_profit=Decimal("0.25"),
            safety_buffer=Decimal("0.10"),
            max_price_impact_bps=100,
        )
    ).check(candidate, quote)
    assert decision.allowed
    assert decision.metadata["net_after_buffer"] == "0.60"


def test_profit_gate_rejects_when_buffer_consumes_profit():
    candidate = _route().to_candidate()
    quote = Quote(
        candidate_id=candidate.candidate_id,
        executable=True,
        input_value=Decimal("100"),
        expected_output_value=Decimal("100.55"),
        estimated_fees=Decimal("0.25"),
        price_impact_bps=40,
    )
    decision = AtomicArbitrageRiskGate(
        AtomicArbitragePolicy(
            min_net_profit=Decimal("0.25"),
            safety_buffer=Decimal("0.10"),
        )
    ).check(candidate, quote)
    assert not decision.allowed
    assert decision.reason == "NET_PROFIT_BELOW_MINIMUM"


def test_price_impact_gate_blocks_before_simulation():
    class Quoter:
        def quote(self, candidate):
            return Quote(
                candidate_id=candidate.candidate_id,
                executable=True,
                input_value=Decimal("100"),
                expected_output_value=Decimal("103"),
                estimated_fees=Decimal("0.20"),
                price_impact_bps=501,
            )

    class Simulator:
        calls = 0

        def simulate(self, candidate, quote):
            self.calls += 1
            return SimulationResult(True, Decimal("2"))

    class Executor:
        calls = 0

        def execute(self, candidate, quote, simulation):
            self.calls += 1
            return ExecutionResult(True, "0xshould-not-happen")

    simulator = Simulator()
    executor = Executor()
    engine = BasicTradingEngine(
        source=AtomicArbitrageSource([_route()]),
        quoter=Quoter(),
        simulator=simulator,
        executor=executor,
        risk_gates=[
            AtomicArbitrageRiskGate(
                AtomicArbitragePolicy(max_price_impact_bps=500)
            )
        ],
        config=EngineConfig(execution_enabled=True),
    )
    result = engine.run_once()
    assert result.status == DecisionStatus.REJECTED
    assert "PRICE_IMPACT_TOO_HIGH" in result.reason
    assert simulator.calls == 0
    assert executor.calls == 0


def test_atomic_strategy_requotes_and_resimulates_in_dry_run():
    class Quoter:
        def __init__(self):
            self.calls = 0

        def quote(self, candidate):
            self.calls += 1
            return Quote(
                candidate_id=candidate.candidate_id,
                executable=True,
                input_value=Decimal("100"),
                expected_output_value=(
                    Decimal("101.20") if self.calls == 1 else Decimal("101.10")
                ),
                estimated_fees=Decimal("0.30"),
                price_impact_bps=40,
                route_id="base-usdc-cycle",
            )

    class Simulator:
        def __init__(self):
            self.calls = 0

        def simulate(self, candidate, quote):
            self.calls += 1
            return SimulationResult(
                ok=True,
                expected_profit=quote.expected_profit,
                transaction_preview={"broadcast": False},
            )

    class Executor:
        def __init__(self):
            self.calls = 0

        def execute(self, candidate, quote, simulation):
            self.calls += 1
            return ExecutionResult(True, "0xshould-not-happen")

    quoter = Quoter()
    simulator = Simulator()
    executor = Executor()
    engine = BasicTradingEngine(
        source=AtomicArbitrageSource([_route()]),
        quoter=quoter,
        simulator=simulator,
        executor=executor,
        risk_gates=[
            AtomicArbitrageRiskGate(
                AtomicArbitragePolicy(
                    min_net_profit=Decimal("0.25"),
                    safety_buffer=Decimal("0.10"),
                    max_price_impact_bps=100,
                )
            )
        ],
        config=EngineConfig(
            execution_enabled=False,
            min_expected_profit=Decimal("0.25"),
        ),
    )

    result = engine.run_once()
    assert result.status == DecisionStatus.DRY_RUN_READY
    assert quoter.calls == 2
    assert simulator.calls == 2
    assert executor.calls == 0
    assert result.quote is not None
    assert result.quote.expected_output_value == Decimal("101.10")
