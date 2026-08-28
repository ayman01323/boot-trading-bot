from __future__ import annotations

"""GPT-only Solana LIVE sizing override.

Owner-requested test size: exactly 0.005 SOL for each GPT Solana ENTRY.

This patch deliberately does *not* change Gemini/Grok sizing, ARMED/LIVE/AUTO
controls, PoolCheck, Jupiter forward/reverse quotes, 3x reverse stress, signed
simulation, reserve protection, position limits, or any exit behaviour.
"""

from decimal import Decimal
from threading import RLock

from . import sibot1_solana_live_bridge_patch as _bridge

GPT_ENTRY_SOL = Decimal("0.005")
_PREV_EXECUTE_ENTRY = _bridge._execute_entry
_SIZE_LOCK = RLock()
_INSTALLED = False


def _execute_entry_gpt_005(app, tid, candidate, key) -> None:
    if str(candidate.get("engine_id") or "").strip().lower() != "gpt":
        return _PREV_EXECUTE_ENTRY(app, tid, candidate, key)

    # The existing bridge computes entry size through _entry_size(), whose
    # default and hard maximum are module globals. Override them only while the
    # GPT entry is being evaluated/executed, then restore them immediately so
    # every other engine keeps its prior sizing semantics.
    with _SIZE_LOCK:
        previous_default = _bridge.DEFAULT_ENTRY_SOL
        previous_hard_max = _bridge.HARD_MAX_ENTRY_SOL
        try:
            _bridge.DEFAULT_ENTRY_SOL = GPT_ENTRY_SOL
            _bridge.HARD_MAX_ENTRY_SOL = GPT_ENTRY_SOL
            return _PREV_EXECUTE_ENTRY(app, tid, candidate, key)
        finally:
            _bridge.DEFAULT_ENTRY_SOL = previous_default
            _bridge.HARD_MAX_ENTRY_SOL = previous_hard_max


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _bridge._execute_entry = _execute_entry_gpt_005
    _INSTALLED = True
    print(
        "[sibot1-gpt-solana-size] installed=true gpt_entry_sol=0.005 "
        "other_engines_unchanged=true poolcheck_unchanged=true simulation_unchanged=true"
    )


install()
