"""Compatibility import for the canonical Solana pool-risk implementation.

The active implementation lives in :mod:`learnerbot.solana_pool_risk_gate`.
This historical module name is retained only so an older import cannot revive a
second, divergent set of HOOD-derived safety rules.
"""

from .solana_pool_risk_gate import (  # noqa: F401
    evaluate_dexscreener,
    evaluate_live_pool_risk,
    evaluate_rugcheck,
    external_pool_check,
    install,
    process_leader_event_with_pool_risk,
    reference_reverse_depth_check,
)

__all__ = [
    "evaluate_dexscreener",
    "evaluate_live_pool_risk",
    "evaluate_rugcheck",
    "external_pool_check",
    "install",
    "process_leader_event_with_pool_risk",
    "reference_reverse_depth_check",
]
