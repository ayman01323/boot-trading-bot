from __future__ import annotations

"""Isolated Cold Zone: expand selected copy leaders from 2 to 5.

Imported after the relaxed-entry overlay so all existing entry/exit/risk settings
remain unchanged.  The only policy change is leaders_per_user=5.
"""

from . import solana_cold_zone_strategy_patch as _cz

PROFILE = "COLD_ZONE_17AUG_V2_RELAXED_ENTRY_L5"
LEADERS_PER_USER = 5
_BASE_SETTINGS = _cz._sol.settings


def settings_leader5(app) -> dict:
    cfg = dict(_BASE_SETTINGS(app))
    cfg["leaders_per_user"] = str(LEADERS_PER_USER)
    cfg["solana_strategy_profile"] = PROFILE
    return cfg


def install() -> None:
    if getattr(_cz, "_cold_zone_leader5_installed", False):
        return
    # refresh_rankings_17aug resolves _cz.settings_cold_zone dynamically, while
    # the rest of the runtime reads _sol.settings. Patch both to the same overlay.
    _cz.settings_cold_zone = settings_leader5
    _cz._sol.settings = settings_leader5
    _cz._cold_zone_leader5_installed = True
    print(
        "[solana-cold-zone-leaders] installed=true "
        f"profile={PROFILE} leaders_per_user={LEADERS_PER_USER} strategy_other_settings=unchanged"
    )


install()
