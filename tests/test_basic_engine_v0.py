from decimal import Decimal

from learnerbot.basic_engine_v0 import (
    BasicTradingEngine,
    Candidate,
    EngineConfig,
    ExecutionResult,
    Quote,
    RiskDecision,
    SimulationResult,
)
from learnerbot.basic_engine_v0.core import DecisionStatus


class Source:
    def scan(self):
        return [Candidate("c1", "test-chain", "triangle", priority=Decimal("1"))]


class Quoter:
    def __init__(self):
        self.calls = 0

    def quote(self, candidate):
        self.calls += 1
        return Quote(
            candidate_id=candidate.candidate_id,
            executable=True,
            input_value=Decimal("100"),
            expected_output_value=Decimal("103"),
            estimated_fees=Decimal("1"),
            route_id="route-1",
        )


class AllowGate:
    name = "allow"

    def check(self, candidate, quote):
        return RiskDecision(True)


class BlockGate:
    name = "rug"

    def check(self, candidate, quote):
        return RiskDecision(False, "POOL_LIQUIDITY_COLLAPSE")


class Simulator:
    def __init__(self):
        self.calls = 0

    def simulate(self, candidate, quote):
        self.calls += 1
        return SimulationResult(ok=True, expected_profit=Decimal("2"))


class Executor:
    def __init__(self):
        self.calls = 0

    def execute(self, candidate, quote, simulation):
        self.calls += 1
        return ExecutionResult(submitted=True, tx_id="0xtest", reason="SUBMITTED")


def test_dry_run_requotes_and_resimulates_without_execution():
    quoter = Quoter()
    simulator = Simulator()
    executor = Executor()
    engine = BasicTradingEngine(
        source=Source(),
        quoter=quoter,
        simulator=simulator,
        executor=executor,
        risk_gates=[AllowGate()],
        config=EngineConfig(execution_enabled=False, min_expected_profit=Decimal("1")),
    )

    result = engine.run_once()

    assert result.status == DecisionStatus.DRY_RUN_READY
    assert quoter.calls == 2
    assert simulator.calls == 2
    assert executor.calls == 0


def test_risk_gate_blocks_before_simulation_and_execution():
    simulator = Simulator()
    executor = Executor()
    engine = BasicTradingEngine(
        source=Source(),
        quoter=Quoter(),
        simulator=simulator,
        executor=executor,
        risk_gates=[BlockGate()],
        config=EngineConfig(execution_enabled=True),
    )

    result = engine.run_once()

    assert result.status == DecisionStatus.REJECTED
    assert "POOL_LIQUIDITY_COLLAPSE" in result.reason
    assert simulator.calls == 0
    assert executor.calls == 0


def test_execution_requires_second_quote_and_second_simulation():
    quoter = Quoter()
    simulator = Simulator()
    executor = Executor()
    engine = BasicTradingEngine(
        source=Source(),
        quoter=quoter,
        simulator=simulator,
        executor=executor,
        risk_gates=[AllowGate()],
        config=EngineConfig(execution_enabled=True, min_expected_profit=Decimal("1")),
    )

    result = engine.run_once()

    assert result.status == DecisionStatus.EXECUTED
    assert result.execution is not None
    assert result.execution.tx_id == "0xtest"
    assert quoter.calls == 2
    assert simulator.calls == 2
    assert executor.calls == 1


def test_bad_top_candidate_does_not_starve_next_candidate():
    class TwoSource:
        def scan(self):
            return [
                Candidate("bad", "test-chain", "triangle", priority=Decimal("2")),
                Candidate("good", "test-chain", "triangle", priority=Decimal("1")),
            ]

    class SelectiveQuoter(Quoter):
        def quote(self, candidate):
            self.calls += 1
            if candidate.candidate_id == "bad":
                return Quote(
                    candidate_id="bad",
                    executable=False,
                    input_value=Decimal("100"),
                    expected_output_value=Decimal("0"),
                )
            return Quote(
                candidate_id="good",
                executable=True,
                input_value=Decimal("100"),
                expected_output_value=Decimal("103"),
                estimated_fees=Decimal("1"),
            )

    engine = BasicTradingEngine(
        source=TwoSource(),
        quoter=SelectiveQuoter(),
        simulator=Simulator(),
        executor=Executor(),
        risk_gates=[AllowGate()],
        config=EngineConfig(execution_enabled=False, min_expected_profit=Decimal("1")),
    )

    result = engine.run_once()

    assert result.status == DecisionStatus.DRY_RUN_READY
    assert result.candidate_id == "good"
