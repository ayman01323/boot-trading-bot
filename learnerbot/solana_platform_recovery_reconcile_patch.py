from __future__ import annotations

from contextlib import closing

from . import solana_live_patch as _live
from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_sibot as _sol


_PREV_PLATFORM_AMOUNT_GATE = _edge._platform_amount_gate
_BLOCK_REASON = "platform amount gate is in recovery mode and another LIVE position is still open"


def _reconcile_open_live_positions(app) -> tuple[int, bool]:
    """Return verified open LIVE count after safe per-user reconciliation.

    The installed capacity reconciler proves a stale row empty only after every
    registered wallet balance query succeeds and every result is zero. RPC
    uncertainty therefore remains fail-closed.
    """
    try:
        with closing(_sol.connect(app)) as conn:
            tids = [
                str(r["telegram_id"] or "")
                for r in conn.execute(
                    "SELECT DISTINCT telegram_id FROM positions WHERE status='OPEN' AND mode='LIVE'"
                ).fetchall()
                if str(r["telegram_id"] or "").strip()
            ]
    except Exception:
        return 0, False

    total = 0
    try:
        for tid in tids:
            total += int(_live._open_live_count(app, tid))
    except Exception:
        return 0, False
    return total, True


def platform_amount_gate(app, cfg: dict):
    ok, reason, metrics, recovery = _PREV_PLATFORM_AMOUNT_GATE(app, cfg)
    if ok or str(reason) != _BLOCK_REASON:
        return ok, reason, metrics, recovery

    verified_open, proven = _reconcile_open_live_positions(app)
    if not proven:
        return False, "cannot prove recovery canary exclusivity after LIVE-position reconciliation", metrics, False
    if verified_open > 0:
        return False, _BLOCK_REASON, metrics, False

    # Reconciliation may have quarantined stale rows as RECONCILE_REQUIRED.
    # Re-run the original gate so its hard PF, cooldown, single-canary and
    # single-armed-user safeguards remain intact.
    return _PREV_PLATFORM_AMOUNT_GATE(app, cfg)


def install() -> None:
    if getattr(_edge, "_platform_recovery_reconcile_installed", False):
        return
    _edge._platform_amount_gate = platform_amount_gate
    _edge._platform_recovery_reconcile_installed = True
    print(
        "[solana-platform-recovery-reconcile] open_live=verified_balance_only "
        "rpc_uncertainty=fails_closed thresholds=unchanged"
    )


install()
