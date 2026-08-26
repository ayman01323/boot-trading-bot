from __future__ import annotations

"""Bounded nomination-only flexibility for SiBot 1 Solana engines.

The independent workers remain SHADOW/PAPER and keyless.  This patch only adjusts
which observed market events Gemini/Grok may nominate for central PoolCheck and the
separate protected LIVE bridge.  Structural token controls, dev-selling unknown,
PoolCheck, reverse sellability, 3x stress, signed simulation, signer permissions,
position limits and LIVE/AUTO controls are not modified.
"""

from dataclasses import replace
from decimal import Decimal

from sibot1_engines.gemini import settings_schema as _gemini
from sibot1_engines.grok import settings_schema as _grok

_INSTALLED = False
_ORIG_GEMINI_LOAD = _gemini.load_settings
_ORIG_GROK_LOAD = _grok.load_settings


def _gemini_load(path):
    s = _ORIG_GEMINI_LOAD(path)
    # Lower only the discovery/nomination floors.  The LIVE bridge independently
    # revalidates liquidity and sellability before any transaction can be signed.
    return replace(
        s,
        min_liquidity_usd=min(s.min_liquidity_usd, Decimal("3000")),
        min_volume_usd=min(s.min_volume_usd, Decimal("100")),
        min_volume_liquidity_ratio=min(s.min_volume_liquidity_ratio, Decimal("0.01")),
    )


def _grok_load(path):
    s = _ORIG_GROK_LOAD(path)
    return replace(
        s,
        min_confidence=min(s.min_confidence, Decimal("0.52")),
        min_volume_velocity=min(s.min_volume_velocity, Decimal("0.005")),
        # Developer selling stays rejected even if an older settings CSV disabled it.
        # The engine's separate "known" flag also remains fail-closed for unknown flow.
        reject_dev_selling=True,
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _gemini.load_settings = _gemini_load
    _grok.load_settings = _grok_load
    _INSTALLED = True
    print(
        "[deep-nomination-relaxation] gemini_liquidity=3000 gemini_volume=100 "
        "gemini_vl_ratio=0.01 grok_confidence=0.52 grok_velocity=0.005 "
        "dev_unknown_fail_closed=true live_safety_unchanged=true",
        flush=True,
    )


install()
