"""Shared BOOT platform infrastructure.

This package contains coordination primitives that are deliberately independent
from wallet/signing and trading execution.  Trading engines may publish
observations here, but the queue never signs, broadcasts, reserves capital, or
changes LIVE/ARMED state.
"""

from .rejected_opportunity_queue import RejectedOpportunityQueue

__all__ = ["RejectedOpportunityQueue"]
