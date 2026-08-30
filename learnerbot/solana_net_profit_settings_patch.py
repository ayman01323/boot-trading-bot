from __future__ import annotations

"""SiLearn executable-net-profit settings tuning.

Owner instruction: 2026-08-30.

Purpose
-------
Use the observed LIVE transaction-cost baseline for fallback fee estimation while
keeping the existing fresh Jupiter executable-exit P/L calculation authoritative.
The 3% immediate round-trip-loss value remains a pre-entry HARD CEILING; it is
not treated as the normal cost of a trade.

Observed basis
--------------
Recent LIVE BUY economic overhead after excluding refundable account rent was
approximately 0.000006137 SOL.  We use 0.0000062 SOL as the rounded baseline per
side until a larger completed-trade sample replaces it.

Exit policy
-----------
* +5% executable NET P/L activates profit protection.
* After +5% has been reached, protect at least +3% executable NET P/L.
* +10% executable NET P/L activates the existing 4 percentage-point trailing gap.
* +15% executable NET P/L remains the normal full take-profit ceiling.
* 30 minutes remains the normal maximum hold; Change Set 4's protected 33-minute
  full-exit attempt remains unchanged.

No signer, simulation, reserve, quote, slippage, transaction-validation,
wallet-binding, kill-switch, PoolCheck, or protected-close hook is changed here.
"""

from . import solana_owner_changeset_4_patch as _owner
from . import solana_sibot as _sol

PATCH_ID = "SILEARN_NET_PROFIT_20260830"
OBSERVED_FEE_BASELINE_SOL = "0.0000062"

_PREV_SETTINGS = _sol.settings

_OVERRIDES = {
    # Measured/fallback economics. LIVE entry accounting still prefers actual
    # wallet delta, and realised LIVE exits still prefer actual wallet delta.
    "estimated_entry_fee_sol": OBSERVED_FEE_BASELINE_SOL,
    "estimated_exit_fee_sol": OBSERVED_FEE_BASELINE_SOL,

    # Keep this as a hard pre-entry route-quality ceiling, not a normal fee.
    "max_roundtrip_loss_pct": "3",

    # Executable NET P/L lifecycle.
    "break_even_trigger_pct": "5",
    "break_even_floor_pct": "3",
    "trailing_trigger_pct": "10",
    "trailing_gap_pct": "4",
    "take_profit_pct": "15",
    "max_hold_hours": "0.5",
}


def settings_silearn_net_profit(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))
    cfg.update(_OVERRIDES)
    cfg["solana_strategy_profile"] = (
        str(cfg.get("solana_strategy_profile") or "")
        + "+SILEARN_NET_EXECUTABLE_PNL_20260830"
    )
    return cfg


def install() -> None:
    if getattr(_sol, "_silearn_net_profit_20260830_installed", False):
        return

    # This patch must sit immediately outside the stamped Change Set 4 settings
    # wrapper so the historic profile remains intact underneath it.
    if _PREV_SETTINGS is not _owner.settings_owner_changeset_4:
        raise RuntimeError(
            "SiLearn net-profit settings refused: Change Set 4 settings wrapper is not the active base"
        )

    _sol.settings = settings_silearn_net_profit
    _sol._silearn_net_profit_20260830_installed = True

    effective = _sol.settings
    if effective is not settings_silearn_net_profit:
        raise RuntimeError("SiLearn net-profit settings failed to install")

    print(
        "[silearn-net-profit-20260830] fee_side=0.0000062SOL "
        "roundtrip_hard_cap=3% protect_trigger=5% protect_floor=3% "
        "trailing=10%/4pp take_profit=15% max_hold=30m"
    )


install()
