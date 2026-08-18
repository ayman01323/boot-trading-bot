from __future__ import annotations

import json
import time
from contextlib import closing
from decimal import Decimal

from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol

_EPOCH_STATE_KEY = "solana_corrected_live_pnl_epoch_v2"
_PREV_COPIED_METRICS = _guard._copied_metrics


def _epoch(app) -> int:
    """Return one persistent cutoff for copied-performance learning.

    Historical LIVE rows remain untouched for reporting, but rows closed before
    this accounting epoch are not used to suspend/select leaders because those
    rows pre-date wallet-bound exits and token-account rent reconciliation.
    """
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        raw = _sol._state(conn, _EPOCH_STATE_KEY, "") or ""
        try:
            value = int(raw)
        except Exception:
            value = 0
        if value <= 0:
            value = int(time.time())
            _sol._set_state(conn, _EPOCH_STATE_KEY, value)
        return value


def _pf(profit: Decimal, loss: Decimal) -> Decimal:
    if loss > 0:
        return profit / loss
    return Decimal("99") if profit > 0 else Decimal(0)


def _copied_metrics_corrected(app, tid, wallet):
    cutoff = _epoch(app)
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT realised_net_sol,closed_at FROM positions
               WHERE telegram_id=? AND leader_wallet=? AND status='CLOSED' AND mode='LIVE'
                 AND closed_at>=?
               ORDER BY closed_at DESC LIMIT 50""",
            (str(tid), str(wallet), int(cutoff)),
        ).fetchall()]
    vals = [(_sol._dec(r.get("realised_net_sol"), 0), int(r.get("closed_at") or 0)) for r in rows]
    profit = sum((n for n, _ in vals if n > 0), Decimal(0))
    loss = sum((-n for n, _ in vals if n < 0), Decimal(0))
    wins = sum(1 for n, _ in vals if n > 0)
    closed = len(vals)
    streak = 0
    for n, _ in vals:
        if n < 0:
            streak += 1
        else:
            break
    return {
        "closed": closed,
        "win_rate": Decimal(wins * 100) / Decimal(closed) if closed else Decimal(0),
        "profit_factor": _pf(profit, loss),
        "consecutive_losses": streak,
        "latest_closed_at": vals[0][1] if vals else 0,
        "accounting_epoch": cutoff,
    }


def _clear_old_suspend_states(app):
    """Clear cooldown state created from pre-correction P&L only once."""
    marker = "solana_corrected_live_pnl_epoch_v2_suspend_cleanup"
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        if _sol._state(conn, marker, ""):
            return
        rows = conn.execute(
            "SELECT key FROM state WHERE key LIKE 'sol_profit_guard_suspend:%'"
        ).fetchall()
        for row in rows:
            _sol._set_state(conn, str(row["key"]), json.dumps({"until": 0, "latest_closed_at": 0}))
        _sol._set_state(conn, marker, int(time.time()))


def install():
    if getattr(_guard, "_corrected_accounting_epoch_installed", False):
        return
    _guard._copied_metrics = _copied_metrics_corrected
    _guard._corrected_accounting_epoch_installed = True
    print("[solana-profit-epoch] corrected_pnl_only=true historical_rows_preserved=true")


# Cleanup needs a real app/data directory, so it is called lazily by the first
# metrics query rather than at module-import time.
_original = _copied_metrics_corrected


def _copied_metrics_with_cleanup(app, tid, wallet):
    _clear_old_suspend_states(app)
    return _original(app, tid, wallet)


install()
_guard._copied_metrics = _copied_metrics_with_cleanup
