from __future__ import annotations

from threading import Lock
from typing import Iterable, Mapping

from .core import Candidate, CandidateSource, DecisionStatus, EngineConfig, EngineDecision, EngineObserver, Executor, Quote, Quoter, RiskGate, Simulator


class BasicTradingEngine:
    """Single-owner, fail-closed, plugin-oriented trading engine."""

    def __init__(self, *, source: CandidateSource, quoter: Quoter, simulator: Simulator, executor: Executor, risk_gates: Iterable[RiskGate] = (), observers: Iterable[EngineObserver] = (), config: EngineConfig | None = None) -> None:
        self.source = source
        self.quoter = quoter
        self.simulator = simulator
        self.executor = executor
        self.risk_gates = tuple(risk_gates)
        self.observers = tuple(observers)
        self.config = config or EngineConfig()
        self._execution_lock = Lock()

    def _emit(self, event: str, payload: Mapping[str, object]) -> None:
        for observer in self.observers:
            try:
                observer.on_event(event, payload)
            except Exception:
                continue

    def _reject(self, candidate: Candidate, reason: str, *, quote: Quote | None = None, simulation=None) -> EngineDecision:
        self._emit("candidate_rejected", {"candidate_id": candidate.candidate_id, "reason": reason})
        return EngineDecision(status=DecisionStatus.REJECTED, candidate_id=candidate.candidate_id, reason=reason, quote=quote, simulation=simulation)

    def _quote(self, candidate: Candidate) -> Quote | None:
        try:
            quote = self.quoter.quote(candidate)
        except Exception as exc:
            self._emit("quote_error", {"candidate_id": candidate.candidate_id, "error": type(exc).__name__})
            return None
        return quote if quote.candidate_id == candidate.candidate_id else None

    def _risk_check(self, candidate: Candidate, quote: Quote) -> str | None:
        for gate in self.risk_gates:
            try:
                decision = gate.check(candidate, quote)
            except Exception as exc:
                return f"RISK_GATE_ERROR:{getattr(gate, 'name', type(gate).__name__)}:{type(exc).__name__}"
            if not decision.allowed:
                return f"RISK_BLOCK:{getattr(gate, 'name', type(gate).__name__)}:{decision.reason}"
        return None

    def evaluate_candidate(self, candidate: Candidate) -> EngineDecision:
        quote = self._quote(candidate)
        if quote is None:
            return self._reject(candidate, "QUOTE_ERROR")
        if not quote.executable:
            return self._reject(candidate, "QUOTE_NOT_EXECUTABLE", quote=quote)
        risk_reason = self._risk_check(candidate, quote)
        if risk_reason:
            return self._reject(candidate, risk_reason, quote=quote)
        try:
            simulation = self.simulator.simulate(candidate, quote)
        except Exception as exc:
            return self._reject(candidate, f"SIMULATION_ERROR:{type(exc).__name__}", quote=quote)
        if not simulation.ok:
            return self._reject(candidate, f"SIMULATION_REJECTED:{simulation.reason}", quote=quote, simulation=simulation)
        if simulation.expected_profit < self.config.min_expected_profit:
            return self._reject(candidate, "PROFIT_BELOW_MINIMUM", quote=quote, simulation=simulation)

        fresh_quote = self._quote(candidate)
        if fresh_quote is None or not fresh_quote.executable:
            return self._reject(candidate, "FRESH_QUOTE_UNAVAILABLE", quote=fresh_quote or quote)
        if self.config.require_same_route_on_recheck and quote.route_id and fresh_quote.route_id != quote.route_id:
            return self._reject(candidate, "ROUTE_CHANGED_ON_RECHECK", quote=fresh_quote)
        risk_reason = self._risk_check(candidate, fresh_quote)
        if risk_reason:
            return self._reject(candidate, risk_reason, quote=fresh_quote)
        try:
            fresh_simulation = self.simulator.simulate(candidate, fresh_quote)
        except Exception as exc:
            return self._reject(candidate, f"FRESH_SIMULATION_ERROR:{type(exc).__name__}", quote=fresh_quote)
        if not fresh_simulation.ok:
            return self._reject(candidate, f"FRESH_SIMULATION_REJECTED:{fresh_simulation.reason}", quote=fresh_quote, simulation=fresh_simulation)
        if fresh_simulation.expected_profit < self.config.min_expected_profit:
            return self._reject(candidate, "FRESH_PROFIT_BELOW_MINIMUM", quote=fresh_quote, simulation=fresh_simulation)

        if not self.config.execution_enabled:
            return EngineDecision(status=DecisionStatus.DRY_RUN_READY, candidate_id=candidate.candidate_id, reason="EXECUTION_DISABLED", quote=fresh_quote, simulation=fresh_simulation)

        with self._execution_lock:
            try:
                result = self.executor.execute(candidate, fresh_quote, fresh_simulation)
            except Exception as exc:
                return EngineDecision(status=DecisionStatus.EXECUTION_FAILED, candidate_id=candidate.candidate_id, reason=f"EXECUTOR_ERROR:{type(exc).__name__}", quote=fresh_quote, simulation=fresh_simulation)
        return EngineDecision(status=DecisionStatus.EXECUTED if result.submitted else DecisionStatus.EXECUTION_FAILED, candidate_id=candidate.candidate_id, reason=result.reason, quote=fresh_quote, simulation=fresh_simulation, execution=result)

    def run_once(self) -> EngineDecision:
        try:
            candidates = list(self.source.scan())
        except Exception:
            return EngineDecision(status=DecisionStatus.NO_CANDIDATE, reason="SCAN_ERROR")
        candidates.sort(key=lambda item: item.priority, reverse=True)
        candidates = candidates[: max(0, self.config.max_candidates_per_cycle)]
        if not candidates:
            return EngineDecision(status=DecisionStatus.NO_CANDIDATE, reason="NO_CANDIDATE")
        last_rejection = None
        for candidate in candidates:
            decision = self.evaluate_candidate(candidate)
            if decision.status in {DecisionStatus.DRY_RUN_READY, DecisionStatus.EXECUTED, DecisionStatus.EXECUTION_FAILED}:
                return decision
            last_rejection = decision
        return last_rejection or EngineDecision(status=DecisionStatus.NO_CANDIDATE, reason="NO_CANDIDATE")
