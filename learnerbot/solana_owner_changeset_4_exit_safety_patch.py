from __future__ import annotations

"""Change Set 4 timed-exit safety classification.

Approval timestamp: 2026-08-29T10:38:58Z (2026-08-29 11:38:58 BST)
Subject: 33-minute full-exit remains a full-position request, but if 100% is
pre-broadcast liquidity-unsafe it may use the existing guarded emergency slice
search/backoff instead of hammering the router every monitor cycle.
"""

from . import solana_emergency_liquidity_unwind_patch as _emergency

CHANGESET4_TIMED_EXIT_REASON = "SOLANA_OWNER_CHANGESET4_33M_FULL_EXIT"


def install() -> None:
    _emergency._LOSS_EXIT_REASONS.add(CHANGESET4_TIMED_EXIT_REASON)
    print(
        "[owner-changeset-4-exit-safety] reason=SOLANA_OWNER_CHANGESET4_33M_FULL_EXIT "
        "request=100% safe_slice_fallback=true exponential_backoff=true impact_bypass=false"
    )


install()
