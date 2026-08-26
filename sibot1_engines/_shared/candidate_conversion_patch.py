from __future__ import annotations

"""Bounded candidate-conversion fixes for SiBot 1.

This module only expands which already-produced market opportunities are visible to
SiBot's SHADOW strategy workers.  It does not alter PoolCheck, signing, broadcast,
position limits, live controls, or protected-bridge revalidation.
"""

from pathlib import Path

from . import market_data as _market

_INSTALLED = False
_ORIG_EVM_INIT = _market.EvmOpportunityCsvSource.__init__


def _evm_init(self, csv_dir, evidence, max_age_seconds: int = 900) -> None:
    _ORIG_EVM_INIT(self, csv_dir, evidence, max_age_seconds=max_age_seconds)
    full_power = Path(csv_dir) / "auto" / "full_power_opportunities.csv"
    paths = list(self.paths)
    if full_power not in paths:
        # The fast/full-power scanner is the production writer used when
        # fast_market_enabled=true.  Treat it as an additional read-only source;
        # the GPT engine and protected bridge still independently reject anything
        # that lacks exact route/liquidity evidence or final LIVE preflight.
        paths.insert(1, full_power)
    self.paths = tuple(paths)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _market.EvmOpportunityCsvSource.__init__ = _evm_init
    _INSTALLED = True


install()
