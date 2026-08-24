"""Basic Trading Engine v0.

An isolated, upgradeable trading-engine core. Nothing in the production
runtime imports this package yet.
"""

from .core import (
    Candidate,
    EngineConfig,
    EngineDecision,
    ExecutionResult,
    Quote,
    RiskDecision,
    SimulationResult,
)
from .engine import BasicTradingEngine
from .factory import build_csv_evm_v2_dry_run_engine

__all__ = [
    "BasicTradingEngine",
    "Candidate",
    "EngineConfig",
    "EngineDecision",
    "ExecutionResult",
    "Quote",
    "RiskDecision",
    "SimulationResult",
    "build_csv_evm_v2_dry_run_engine",
]
