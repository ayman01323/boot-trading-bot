from __future__ import annotations

"""Bounded candidate-conversion fixes for SiBot 1.

This module only expands which already-produced market opportunities are visible to
SiBot's SHADOW strategy workers. It does not alter PoolCheck, signing, broadcast,
position limits, live controls, or protected-bridge revalidation.
"""

from pathlib import Path

from . import market_data as _market

_INSTALLED = False
_ORIG_EVM_INIT = _market.EvmOpportunityCsvSource.__init__


def _evm_init(self, csv_dir, evidence, max_age_seconds: int = 900) -> None:
    _ORIG_EVM_INIT(self, csv_dir, evidence, max_age_seconds=max_age_seconds)
    auto_dir = Path(csv_dir) / "auto"
    base_full_power = auto_dir / "base_full_power_opportunities.csv"
    full_power = auto_dir / "full_power_opportunities.csv"
    paths = list(self.paths)
    # The dedicated Base feed is written from the same existing Base scan and is
    # not overwritten by slower chains. It carries the original quote timestamp,
    # so GPT's independent <=15s freshness gate remains authoritative.
    for candidate in (base_full_power, full_power):
        if candidate not in paths:
            paths.insert(1, candidate)
    self.paths = tuple(paths)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _market.EvmOpportunityCsvSource.__init__ = _evm_init
    _INSTALLED = True


install()
