"""Basic Trading Engine v0.

The generic core is upgradeable. Production integration is installed by
`main_patch`, which preserves the hardened current execution path while making
v0 the primary EVM AUTO orchestration entrypoint.
"""

from .core import Candidate, EngineConfig, EngineDecision, ExecutionResult, Quote, RiskDecision, SimulationResult
from .engine import BasicTradingEngine

__all__ = [
    "BasicTradingEngine",
    "Candidate",
    "EngineConfig",
    "EngineDecision",
    "ExecutionResult",
    "Quote",
    "RiskDecision",
    "SimulationResult",
]
