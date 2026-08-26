"""GPT-owned shared contracts for SiBot 1.

Do not place agent-specific strategy logic in this package.
"""

from .contracts import ExitIntent, MarketEvent, SiBot1Engine, TradeIntent
from .candidate_conversion_patch import install as _install_candidate_conversion_patch

_install_candidate_conversion_patch()

__all__ = ["MarketEvent", "TradeIntent", "ExitIntent", "SiBot1Engine"]
