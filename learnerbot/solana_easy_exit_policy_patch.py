from __future__ import annotations

"""Exit-only policy overlay for Solana LIVE copied positions.

The later first-day strategy restore intentionally put the historical 10% stop /
25% take-profit thresholds back into the active settings. This module restores
only the earlier easier EXIT behaviour while leaving the current leader-quality,
entry-edge, PoolCheck, reserve, simulation, signing and transaction-validation
stack untouched.

Two exit-liveness defects are also corrected here:
1. an already-open real position remains risk-managed even if the account's LIVE
   entry switch is later turned off; disabling new risk must not strand old risk;
2. if a fresh valuation temporarily fails, a previously measured stop-loss breach
   may still request a liquidity-capped emergency unwind instead of being silently
   skipped forever. Fresh take-profit/trailing decisions still require valuation.
"""

import time
from contextlib import closing
from decimal import Decimal

from . import solana_emergency_liquidity_unwind_patch as _emergency
from . import solana_live_patch as _live
from . import solana_sibot as _sol

_PREV_SETTINGS = _sol.settings
_PREV_MONITOR_POSITIONS = _sol.monitor_positions

# These are policy ceilings/floors, not configurable relaxations. Existing CSV
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
_STALE_STOP_REASON = "SOLANA_STOP_LOSS_LAST_KNOWN_FALLBACK"


def _d(value, default="0") -> Decimal:
    return _sol._dec(value, default)


def _i(value, default=0) -> int:
    return _sol._int(value, default)


def settings_easy_exit(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))
    cfg["stop_loss_pct"] = str(min(max(Decimal(0), _d(cfg.get("stop_loss_pct"), 5)), Decimal("5")))
    cfg["take_profit_pct"] = str(min(max(Decimal(0), _d(cfg.get("take_profit_pct"), 10)), Decimal("10")))
    cfg["leader_exit_loss_cap_pct"] = "0"
    cfg["break_even_trigger_pct"] = str(min(max(Decimal(0), _d(cfg.get("break_even_trigger_pct"), 3)), Decimal("3")))
    cfg["break_even_floor_pct"] = str(max(Decimal("0.25"), _d(cfg.get("break_even_floor_pct"), "0.25")))
    cfg["trailing_trigger_pct"] = str(min(max(Decimal(0), _d(cfg.get("trailing_trigger_pct"), 5)), Decimal("5")))
    cfg["trailing_gap_pct"] = str(min(max(Decimal("0.10"), _d(cfg.get("trailing_gap_pct"), 2)), Decimal("2")))
    cfg["position_poll_seconds"] = str(min(max(1, _i(cfg.get("position_poll_seconds"), 10)), 10))
    cfg["easy_exit_policy"] = "true"
    return cfg


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


def _exit_reconciling(position: dict) -> bool:
    reason = str(position.get("exit_reason") or "")
    return reason.startswith("EXIT_CIRCUIT_RECONCILING")


def _persist_valuation(app, position: dict, ev: dict, current: Decimal, peak: Decimal) -> None:
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            conn.execute(
                """UPDATE positions
                   SET current_exit_sol=?,unrealised_net_sol=?,unrealised_pct=?,
                       peak_unrealised_pct=?,updated_at=?
                   WHERE position_id=? AND status='OPEN'""",
                (
                    str(ev.get("proceeds_sol") or 0),
                    str(ev.get("net_sol") or 0),
                    float(current),
                    float(peak),
                    int(time.time()),
                    str(position.get("position_id") or ""),
                ),
            )
            conn.commit()
    except Exception:
        pass


def _fresh_exit_reason(app, position: dict, cfg: dict) -> tuple[str | None, dict | None]:
    try:
        ev = _sol.evaluate_position(app, position)
    except Exception as exc:
        print(
            "[solana-easy-exit] valuation_unavailable position=%s type=%s"
            % (str(position.get("position_id") or ""), type(exc).__name__),
            flush=True,
        )
        return None, None

    current = _d(ev.get("net_pct"), 0)
    peak = max(_d(position.get("peak_unrealised_pct"), 0), current)
    _persist_valuation(app, position, ev, current, peak)

    if int(position.get("leader_exit_pending") or 0):
        return _PENDING_REASON, ev
    if peak >= _d(cfg.get("break_even_trigger_pct"), 3) and current <= _d(cfg.get("break_even_floor_pct"), ".25"):
        return "SOLANA_BREAK_EVEN_PROTECT", ev
    if peak >= _d(cfg.get("trailing_trigger_pct"), 5):
        floor = max(_d(cfg.get("break_even_floor_pct"), ".25"), peak - _d(cfg.get("trailing_gap_pct"), 2))
        if current <= floor:
            return "SOLANA_TRAILING_PROFIT_PROTECT", ev
    if current <= -_d(cfg.get("stop_loss_pct"), 5):
        return "SOLANA_STOP_LOSS", ev
    if current >= _d(cfg.get("take_profit_pct"), 10):
        return "SOLANA_TAKE_PROFIT", ev
    age_h = Decimal(max(0, int(time.time()) - _i(position.get("entry_ts"), int(time.time())))) / Decimal(3600)
    if age_h >= _d(cfg.get("max_hold_hours"), 24) and current > 0:
        return "SOLANA_MAX_HOLD_PROFIT", ev
    return None, ev


def _close_guarded(app, position: dict, reason: str) -> dict | None:
    tid = str(position.get("telegram_id") or "")
    if not tid:
        return None
    try:
        result = _live._close_live(app, tid, position, Decimal(1), reason)
        return dict(result or {})
    except Exception as exc:
        print(
            "[solana-easy-exit] close_error position=%s reason=%s type=%s"
            % (str(position.get("position_id") or ""), reason, type(exc).__name__),
            flush=True,
        )
        return None


def _manage_remaining_exposure(app) -> None:
    cfg = _sol.settings(app)
    stop = _d(cfg.get("stop_loss_pct"), 5)

    # Re-scan after the normal monitor. Positions successfully closed by the
    # existing path are therefore absent; exit-circuit reconciliation remains
    # authoritative and is never double-submitted here.
    for position in _open_live_positions(app):
        if _exit_reconciling(position):
            continue

        if int(position.get("leader_exit_pending") or 0):
            _close_guarded(app, position, _PENDING_REASON)
            continue

        tid = str(position.get("telegram_id") or "")
        live_entries_enabled = bool(tid and _live.live_enabled(app, tid))

        # When LIVE entries remain enabled, the inner monitor already owns normal
        # fresh-valued exits. We only cover its silent valuation-failure hole.
        if live_entries_enabled:
            try:
                _sol.evaluate_position(app, position)
                continue
            except Exception as exc:
                stored = _d(position.get("unrealised_pct"), 0)
                print(
                    "[solana-easy-exit] inner_valuation_gap position=%s stored_pct=%s type=%s"
                    % (str(position.get("position_id") or ""), str(stored), type(exc).__name__),
                    flush=True,
                )
                if stored <= -stop:
                    _close_guarded(app, position, _STALE_STOP_REASON)
                continue

        # LIVE-off blocks new entries, not risk reduction for exposure that already
        # exists. Use a fresh valuation when available and apply the same easy-exit
        # policy. If valuation is unavailable, only a stored stop-loss breach may
        # use the emergency fallback; profit exits never use stale valuation.
        reason, ev = _fresh_exit_reason(app, position, cfg)
        if reason:
            _close_guarded(app, position, reason)
            continue
        if ev is None:
            stored = _d(position.get("unrealised_pct"), 0)
            if stored <= -stop:
                _close_guarded(app, position, _STALE_STOP_REASON)


def monitor_positions_easy_exit(app):
    result = _PREV_MONITOR_POSITIONS(app)
    _manage_remaining_exposure(app)
    return result


def install() -> None:
    if getattr(_sol, "_easy_exit_policy_installed", False):
        return

    # Existing emergency exit cap/ladders remain authoritative. These additional
    # reasons merely opt already-triggered risk exits into the same safe slicing.
    _emergency._LOSS_EXIT_REASONS.add(_PENDING_REASON)
    _emergency._LOSS_EXIT_REASONS.add(_STALE_STOP_REASON)

    _sol.settings = settings_easy_exit
    _sol.monitor_positions = monitor_positions_easy_exit
    _sol._easy_exit_policy_installed = True
    print(
        "[solana-easy-exit] active=true stop<=5% take_profit<=10% break_even<=3% "
        "trailing_trigger<=5% trailing_gap<=2% leader_exit_pending=retry_any_pnl "
        "existing_exposure_managed_when_live_off=true stale_stop_fallback=true "
        "partial_sell_profit_guard=preserved emergency_impact_cap=unchanged",
        flush=True,
    )


install()
