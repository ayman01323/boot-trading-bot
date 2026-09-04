from __future__ import annotations

"""Isolated Google learner policy: follower exits are always 100%.

The learner previously mirrored a leader's partial SELL percentage. That creates
multiple follower transactions, repeated fixed fees and additional RPC exposure.
The owner strategy is now explicit: any leader SELL means close the follower's
entire remaining position. Existing liquidity, simulation, fee, transaction
validation and exit-circuit safeguards remain authoritative.
"""

from . import solana_sibot as _sol

_PREV_PROCESS = _sol.process_leader_event
_PREV_SETTINGS = _sol.settings


def settings_full_exit_only(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))
    # Document the policy for every downstream reader. The event wrapper below is
    # the enforcement layer; this also prevents any legacy direct partial branch
    # from opting back into mirror behaviour.
    cfg["mirror_partial_sells"] = "false"
    cfg["learner_full_exit_only"] = "true"
    return cfg


def process_leader_event_full_exit_only(app, event: dict):
    action = str((event or {}).get("action") or "").upper()
    if action != "SELL":
        return _PREV_PROCESS(app, event)

    forced = dict(event or {})
    original_pct = _sol._float(forced.get("sell_pct"), 100)
    forced["leader_original_sell_pct"] = original_pct
    forced["sell_pct"] = 100.0
    forced["follower_exit_policy"] = "FULL_EXIT_ONLY"
    return _PREV_PROCESS(app, forced)


def install() -> None:
    if getattr(_sol, "_learner_full_exit_only_installed", False):
        return
    _sol.settings = settings_full_exit_only
    _sol.process_leader_event = process_leader_event_full_exit_only
    _sol._learner_full_exit_only_installed = True
    print(
        "[learner-full-exit-only] active=true leader_sell_any_pct=follower_sell_100pct "
        "mirror_partial_sells=false safety_gates=unchanged",
        flush=True,
    )


install()
