from __future__ import annotations

"""Exit-only policy overlay for Solana LIVE copied positions.

The later first-day strategy restore intentionally put the historical 10% stop /
25% take-profit thresholds back into the active settings.  This module restores
only the earlier easier EXIT behaviour while leaving the current leader-quality,
entry-edge, PoolCheck, reserve, simulation, signing and transaction-validation
stack untouched.

Full leader SELLs already travel through the audited immediate leader-exit chain.
If that first full exit is blocked before execution, the position is marked
``leader_exit_pending``.  The old monitor retried such a position only after its
P&L became non-positive.  This overlay also retries that already-requested full
exit regardless of P&L, using the existing liquidity-adaptive emergency ladder.
No new exit trigger is created: only a position whose leader has already exited
and whose first close attempt was blocked is eligible for this retry path.
"""

import time
from contextlib import closing
from decimal import Decimal

from . import solana_emergency_liquidity_unwind_patch as _emergency
from . import solana_live_patch as _live
from . import solana_sibot as _sol

_PREV_SETTINGS = _sol.settings
_PREV_MONITOR_POSITIONS = _sol.monitor_positions

# These are policy ceilings/floors, not configurable relaxations.  Existing CSV
# values can make an exit even easier, but cannot make it harder than this layer.
EASY_EXIT_LIMITS = {
    "stop_loss_pct": "5",
    "take_profit_pct": "10",
    "leader_exit_loss_cap_pct": "0",
    "break_even_trigger_pct": "3",
    "break_even_floor_pct": "0.25",
    "trailing_trigger_pct": "5",
    "trailing_gap_pct": "2",
    "position_poll_seconds": "10",
}

_PENDING_REASON = "SOLANA_LEADER_EXIT_PENDING"


def _d(value, default="0") -> Decimal:
    return _sol._dec(value, default)


def _i(value, default=0) -> int:
    return _sol._int(value, default)


def settings_easy_exit(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))

    # Easier/faster downside and profit capture only.  No BUY-side key is changed.
    cfg["stop_loss_pct"] = str(min(max(Decimal(0), _d(cfg.get("stop_loss_pct"), 5)), Decimal("5")))
    cfg["take_profit_pct"] = str(min(max(Decimal(0), _d(cfg.get("take_profit_pct"), 10)), Decimal("10")))
    cfg["leader_exit_loss_cap_pct"] = "0"
    cfg["break_even_trigger_pct"] = str(min(max(Decimal(0), _d(cfg.get("break_even_trigger_pct"), 3)), Decimal("3")))
    cfg["break_even_floor_pct"] = str(max(Decimal("0.25"), _d(cfg.get("break_even_floor_pct"), "0.25")))
    cfg["trailing_trigger_pct"] = str(min(max(Decimal(0), _d(cfg.get("trailing_trigger_pct"), 5)), Decimal("5")))
    cfg["trailing_gap_pct"] = str(min(max(Decimal("0.10"), _d(cfg.get("trailing_gap_pct"), 2)), Decimal("2")))
    # Never slow the current position-risk monitor as part of a presentation/reporting change.
    cfg["position_poll_seconds"] = str(min(max(1, _i(cfg.get("position_poll_seconds"), 10)), 10))
    cfg["easy_exit_policy"] = "true"
    return cfg


def _pending_live_positions(app) -> list[dict]:
    try:
        with closing(_sol.connect(app)) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT * FROM positions
                       WHERE status='OPEN' AND mode='LIVE' AND leader_exit_pending=1
                       ORDER BY updated_at,position_id"""
                ).fetchall()
            ]
    except Exception:
        return []


def _retry_pending_leader_exit(app, position: dict) -> dict | None:
    """Retry an already-requested leader exit without waiting for follower loss.

    The emergency unwind wrapper remains authoritative.  It may close 100%, use a
    safe smaller slice, or return ``deferred`` without broadcasting anything when
    no candidate clears the unchanged automatic impact/slippage ceiling.
    """
    tid = str(position.get("telegram_id") or "")
    if not tid:
        return None
    try:
        result = _live._close_live(app, tid, position, Decimal(1), _PENDING_REASON)
    except Exception as exc:
        # The underlying close/circuit layers retain their own execution-fault
        # handling.  This outer policy retry must never crash the monitor worker.
        print(
            "[solana-easy-exit] pending_retry_error position=%s type=%s"
            % (str(position.get("position_id") or ""), type(exc).__name__),
            flush=True,
        )
        return None

    result = dict(result or {})
    if bool(result.get("deferred")):
        return result

    # A liquidity-adaptive partial close deliberately leaves leader_exit_pending
    # set on the remaining position.  A full close clears it in the audited close
    # implementation.  No DB mutation is needed here.
    return result


def monitor_positions_easy_exit(app):
    # First run all existing valuation, stop/take-profit, break-even, trailing,
    # reconciliation and liquidity-health logic exactly as composed before us.
    result = _PREV_MONITOR_POSITIONS(app)

    # Then retry any still-open full leader exit intent even while follower P&L is
    # positive.  Durable emergency backoff prevents repeated quote/broadcast load.
    for position in _pending_live_positions(app):
        _retry_pending_leader_exit(app, position)
    return result


def install() -> None:
    if getattr(_sol, "_easy_exit_policy_installed", False):
        return

    # Let the existing 5%-capped adaptive slice ladder recognise this as a risk
    # exit.  100/75/50/25/10/5/2/1% candidates still pass the same simulation,
    # liquidity, fee and pre-broadcast validation stack.
    _emergency._LOSS_EXIT_REASONS.add(_PENDING_REASON)

    _sol.settings = settings_easy_exit
    _sol.monitor_positions = monitor_positions_easy_exit
    _sol._easy_exit_policy_installed = True
    print(
        "[solana-easy-exit] active=true stop<=5% take_profit<=10% break_even<=3% "
        "trailing_trigger<=5% trailing_gap<=2% leader_exit_pending=retry_any_pnl "
        "partial_sell_profit_guard=preserved emergency_impact_cap=unchanged",
        flush=True,
    )


install()
