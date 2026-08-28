from __future__ import annotations

"""Downside-only Solana LIVE loss containment.

This layer is deliberately asymmetric:
- losers: a <=5% economic loss becomes a sticky HARD_EXIT_REQUIRED state until
  the position is actually closed or reconciled;
- winners: no low fixed take-profit is introduced. Winners are allowed to run,
  with only a later break-even/trailing floor.

It never enables LIVE trading, never changes position size, never bypasses
liquidity/impact/fee/simulation/signing guards, and never clears an exit-required
state merely because a later valuation rebounds.
"""

import json
import time
from contextlib import closing
from decimal import Decimal

from . import solana_easy_exit_policy_patch as _easy  # installs the <=5% base stop
from . import solana_emergency_liquidity_unwind_patch as _emergency
from . import solana_live_patch as _live
from . import solana_sibot as _sol

_PREV_SETTINGS = _sol.settings
_PREV_MONITOR_POSITIONS = _sol.monitor_positions

_HARD_EXIT_REASON = "SOLANA_HARD_EXIT_REQUIRED"
_STATE_PREFIX = "solana_loss_containment:"

_sol.DEFAULTS.update({
    "loss_containment_stop_pct": (
        "5",
        "Maximum economic loss before Solana LIVE position becomes sticky HARD_EXIT_REQUIRED",
    ),
    "loss_containment_winner_take_profit_pct": (
        "100",
        "High fixed take-profit floor so ordinary winners are not cut at +10%",
    ),
    "loss_containment_break_even_trigger_pct": (
        "10",
        "Winner gain required before break-even protection may arm",
    ),
    "loss_containment_trailing_trigger_pct": (
        "20",
        "Winner gain required before trailing-profit protection may arm",
    ),
    "loss_containment_trailing_gap_pct": (
        "10",
        "Minimum trailing gap once winner trailing protection is armed",
    ),
})


def _d(value, default="0") -> Decimal:
    return _sol._dec(value, default)


def _state_key(position_id: str) -> str:
    return _STATE_PREFIX + str(position_id)


def settings_loss_containment(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))

    # Downside can never be looser than 5% through this layer.
    stop = min(
        Decimal("5"),
        max(Decimal("0.10"), _d(cfg.get("loss_containment_stop_pct"), "5")),
        max(Decimal("0.10"), _d(cfg.get("stop_loss_pct"), "5")),
    )
    cfg["stop_loss_pct"] = str(stop)

    # Preserve BvKg-style upside. The earlier easy-exit overlay capped take-profit
    # at 10%; this final safety layer deliberately removes that low fixed ceiling.
    winner_tp = max(
        Decimal("100"),
        _d(cfg.get("loss_containment_winner_take_profit_pct"), "100"),
    )
    cfg["take_profit_pct"] = str(winner_tp)

    # Profit protection arms only after a meaningful winner exists. This prevents
    # normal early volatility from clipping a BvKg-style move while still stopping
    # a large winner from round-tripping into a large loss.
    cfg["break_even_trigger_pct"] = str(max(
        Decimal("10"),
        _d(cfg.get("loss_containment_break_even_trigger_pct"), "10"),
    ))
    cfg["trailing_trigger_pct"] = str(max(
        Decimal("20"),
        _d(cfg.get("loss_containment_trailing_trigger_pct"), "20"),
    ))
    cfg["trailing_gap_pct"] = str(max(
        Decimal("10"),
        _d(cfg.get("loss_containment_trailing_gap_pct"), "10"),
    ))
    cfg["loss_containment_active"] = "true"
    return cfg


def _load_state(app, position_id: str) -> dict:
    try:
        with closing(_sol.connect(app)) as conn:
            raw = _sol._state(conn, _state_key(position_id), "") or ""
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _save_state(app, position_id: str, state: dict) -> None:
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            _sol._set_state(
                conn,
                _state_key(position_id),
                json.dumps(state or {}, separators=(",", ":"), default=str),
            )
    except Exception:
        pass


def _mark_required(app, position: dict, observed_pct: Decimal, source: str) -> dict:
    pid = str(position.get("position_id") or "")
    state = _load_state(app, pid)
    if state.get("required"):
        return state
    now = int(time.time())
    state = {
        "required": True,
        "triggered_at": now,
        "trigger_pct": str(observed_pct),
        "source": str(source),
        "attempts": 0,
    }
    _save_state(app, pid, state)
    print(
        "[solana-loss-containment] HARD_EXIT_REQUIRED position=%s pct=%s source=%s"
        % (pid, str(observed_pct), str(source)),
        flush=True,
    )
    return state


def _clear_state(app, position_id: str) -> None:
    _save_state(app, position_id, {})


def _open_live_positions(app) -> list[dict]:
    try:
        with closing(_sol.connect(app)) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT * FROM positions
                       WHERE status='OPEN' AND mode='LIVE'
                       ORDER BY updated_at,position_id"""
                ).fetchall()
            ]
    except Exception:
        return []


def _still_open(app, position_id: str) -> bool:
    try:
        with closing(_sol.connect(app)) as conn:
            row = conn.execute(
                "SELECT status FROM positions WHERE position_id=?",
                (str(position_id),),
            ).fetchone()
        return bool(row and str(row[0]).upper() == "OPEN")
    except Exception:
        return True


def _attempt_required_exit(app, position: dict, state: dict) -> None:
    pid = str(position.get("position_id") or "")
    tid = str(position.get("telegram_id") or "")
    if not pid or not tid:
        return

    state = dict(state or {})
    state["attempts"] = int(state.get("attempts") or 0) + 1
    state["last_attempt_at"] = int(time.time())
    _save_state(app, pid, state)

    try:
        result = dict(
            _live._close_live(app, tid, position, Decimal(1), _HARD_EXIT_REASON)
            or {}
        )
    except Exception as exc:
        print(
            "[solana-loss-containment] hard_exit_retry position=%s type=%s"
            % (pid, type(exc).__name__),
            flush=True,
        )
        return

    # Emergency safe-slicing may reduce exposure without fully closing it. The
    # sticky state remains until chain/database reconciliation says OPEN is gone.
    if bool(result.get("closed")) or not _still_open(app, pid):
        _clear_state(app, pid)
        print(
            "[solana-loss-containment] hard_exit_complete position=%s" % pid,
            flush=True,
        )


def _enforce_sticky_exits(app) -> None:
    cfg = _sol.settings(app)
    stop = min(Decimal("5"), max(Decimal("0.10"), _d(cfg.get("stop_loss_pct"), "5")))

    for position in _open_live_positions(app):
        pid = str(position.get("position_id") or "")
        if not pid:
            continue
        state = _load_state(app, pid)
        if not state.get("required"):
            stored_pct = _d(position.get("unrealised_pct"), "0")
            if stored_pct <= -stop:
                state = _mark_required(app, position, stored_pct, "stored_or_fresh_pnl")
        if state.get("required"):
            _attempt_required_exit(app, position, state)


def monitor_positions_loss_containment(app):
    # Let the established monitor refresh valuation and execute any normal safe
    # close first. Then enforce sticky downside state on anything still OPEN.
    result = _PREV_MONITOR_POSITIONS(app)
    _enforce_sticky_exits(app)
    return result


def install() -> None:
    if getattr(_sol, "_loss_containment_installed", False):
        return

    # The same audited emergency impact ceiling/safe-slice machinery applies.
    # This adds a reason, not a bypass.
    _emergency._LOSS_EXIT_REASONS.add(_HARD_EXIT_REASON)

    _sol.settings = settings_loss_containment
    _sol.monitor_positions = monitor_positions_loss_containment
    _sol._loss_containment_installed = True
    print(
        "[solana-loss-containment] active=true sticky_stop<=5% "
        "winner_fixed_tp>=100% break_even_trigger>=10% trailing_trigger>=20% "
        "trailing_gap>=10% emergency_impact_cap=unchanged live_enable_unchanged=true "
        "position_size_unchanged=true",
        flush=True,
    )


install()
