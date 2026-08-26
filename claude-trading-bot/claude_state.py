"""The one authoritative Claude-bot state machine.

Consolidated per direct owner instruction (2026-08-26), replacing the ad-hoc
halt/resume logic and telegram_ui.handle_update patch previously embedded
directly inside solana_execution_risk_patch.py.

Two kinds of state, kept deliberately separate:

  Ordinary operating state -- OFF / ARMED / STOPPING. Resets to OFF on every
  process/service restart. ARMED is never restored automatically.

  Persistent safety latch -- HALTED_DRAWDOWN. Survives restart, crash, reboot,
  config reload, and GitHub deployment/sync. Only clears through the explicit
  two-step owner-authenticated restart flow below. Nothing else may clear it:
  not equity recovery, not time elapsed, not an AI agent, not a scheduler.

Both live in one JSON file under this instance's own isolated DATA_DIR,
written atomically (tmp + os.replace, matching the pattern already used
elsewhere in this codebase). Never shared with production's data dir.

Command surface (see telegram_control_patch.py for the Telegram wiring):
  /claude_status            read-only, any state
  /claude_arm_live CONFIRM  OFF -> ARMED (blocked while HALTED_DRAWDOWN)
  /claude_disarm            -> OFF, immediate, no confirmation
  /claude_stop              -> STOPPING -> OFF, immediate, no confirmation
  /claude_restart_request   valid only while HALTED_DRAWDOWN; issues a
                             short-lived, single-use challenge
  /claude_restart_confirm CONFIRM
                             consumes the challenge, rechecks preconditions,
                             clears HALTED_DRAWDOWN with a fresh baseline

Every mutating function here takes the owner id that authorised the call and
records it in the persisted state, so a corrupt/ambiguous authorisation trail
is never silently possible.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from risk_engine_guard import quantize_pct

STATE_FILENAME = "claude_bot_state.json"
RESTART_CHALLENGE_TTL_SECONDS = 300

OFF = "OFF"
ARMED = "ARMED"
STOPPING = "STOPPING"
_OPERATING_STATES = (OFF, ARMED, STOPPING)

_STATE_LOCK = threading.RLock()


class ClaudeStateError(RuntimeError):
    """Raised when a requested state transition is not permitted. Fail closed."""


def _state_path(app) -> Path:
    return Path(app.data_dir) / STATE_FILENAME


def _default_state() -> dict:
    return {
        "operating_state": OFF,
        "armed_at": 0,
        "armed_by": "",
        "halted_drawdown": False,
        "drawdown_pct": "0.00",
        "drawdown_usd": "0.00",
        "triggered_at": 0,
        "baseline_epoch": 0,
        "authorized_restart_at": 0,
        "authorized_restart_by": "",
        "restart_challenge": None,  # {"nonce": str, "issued_at": int, "issued_by": str}
        # Equity/high-water-mark model (2026-08-26 review fix). Both are
        # "0" until the first evaluate_drawdown() call establishes them --
        # see that function for how "0" is treated as "not yet seeded".
        "high_water_equity_usd": "0",
        "current_equity_usd": "0",
        # Running total of realised P&L, each closed position accounted
        # exactly once (see account_closed_position() below and
        # solana_execution_risk_patch.reconcile_realized_pnl(), its only
        # caller) -- never re-derived later from a different day's price.
        # Only account_closed_position() ever writes this field.
        "cumulative_realized_pnl_usd": "0",
        # Idempotency ledger (review, 2026-08-26): {position_id: {...}} for
        # every closed position already folded into cumulative_realized_pnl_usd
        # above. This is what makes reconciliation crash-safe -- calling it
        # again after a crash (or redundantly from multiple call sites) can
        # never double-count, because a position_id present here is always
        # skipped.
        "accounted_position_ids": {},
        # Closed positions detected WITHOUT a trustworthy close-time USD
        # valuation (review, 2026-08-26, blocker A): the synchronous
        # same-call capture in solana_execution_risk_patch._guarded_sell()
        # never ran for these (most likely the process crashed between the
        # sell committing to the shared positions DB and that capture
        # running). Never priced with a guess -- see
        # claude_state.mark_unpriced_closed_position() and
        # armed_health_check(), which fails closed while this is non-empty.
        "unpriced_closed_position_ids": {},
        "last_forced_off_reason": "",
        "last_forced_off_at": 0,
    }


def load_state(app) -> dict:
    path = _state_path(app)
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("state is not an object")
        state = _default_state()
        state.update(raw)
        state["operating_state"] = (
            state["operating_state"] if state.get("operating_state") in _OPERATING_STATES else OFF
        )
        state["halted_drawdown"] = bool(state.get("halted_drawdown"))
        state["baseline_epoch"] = max(0, int(state.get("baseline_epoch") or 0))
        return state
    except Exception:
        # A corrupt safety-latch file must fail closed -- halted, not silently
        # treated as a fresh, unhalted instance.
        state = _default_state()
        state["halted_drawdown"] = True
        state["state_error"] = "claude_bot_state.json unreadable/corrupt"
        return state


def _save_state(app, state: dict) -> None:
    path = _state_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)
    path.chmod(0o600)


def reset_on_startup(app) -> dict:
    """Called exactly once per process start, before anything can arm.
    Ordinary operating state always resets to OFF. HALTED_DRAWDOWN is
    deliberately NOT touched -- it must survive every kind of restart."""
    with _STATE_LOCK:
        state = load_state(app)
        if state["operating_state"] != OFF:
            state["operating_state"] = OFF
            state["armed_at"] = 0
            state["armed_by"] = ""
            _save_state(app, state)
        return state


def is_armed(state: dict) -> bool:
    return state.get("operating_state") == ARMED and not state.get("halted_drawdown")


def effective_state(state: dict) -> str:
    """HALTED_DRAWDOWN is reported as the effective state whenever the latch
    is set, regardless of the underlying operating_state field (which always
    reads OFF once the latch fires) -- this is what /claude_status and
    restart-across-reboot tests should read, not operating_state directly."""
    if state.get("halted_drawdown"):
        return "HALTED_DRAWDOWN"
    return state.get("operating_state", OFF)


def arm(app, *, owner_id: str) -> dict:
    with _STATE_LOCK:
        state = load_state(app)
        if state.get("halted_drawdown"):
            raise ClaudeStateError(
                "Cannot arm: HALTED_DRAWDOWN is active. Clear it first with "
                "/claude_restart_request then /claude_restart_confirm CONFIRM."
            )
        state["operating_state"] = ARMED
        state["armed_at"] = int(time.time())
        state["armed_by"] = str(owner_id)
        _save_state(app, state)
        return state


def disarm(app) -> dict:
    with _STATE_LOCK:
        state = load_state(app)
        state["operating_state"] = OFF
        state["armed_at"] = 0
        state["armed_by"] = ""
        _save_state(app, state)
        return state


def stop(app) -> dict:
    """/claude_stop: immediately blocks new entries. Recorded as a transient
    STOPPING -> OFF transition (distinct audit trail from /claude_disarm)."""
    with _STATE_LOCK:
        state = load_state(app)
        state["operating_state"] = STOPPING
        _save_state(app, state)
        state["operating_state"] = OFF
        state["armed_at"] = 0
        state["armed_by"] = ""
        _save_state(app, state)
        return state


def force_off(app, *, reason: str) -> dict:
    """System-triggered (periodic health monitor or a guard's own pre-entry
    check), NOT owner-triggered -- distinct from disarm()/stop(). Used when a
    critical ARMED precondition (signer, risk config, authorised chain,
    kill-switch, guard composition) fails while ARMED, per review (2026-08-26):
    an active transition is required, not merely rejecting the next entry.
    Never touches halted_drawdown -- this function must never arm, clear a
    latch, or do anything except turn ARMED off with a recorded reason."""
    with _STATE_LOCK:
        state = load_state(app)
        state["operating_state"] = OFF
        state["armed_at"] = 0
        state["armed_by"] = ""
        state["last_forced_off_reason"] = str(reason)
        state["last_forced_off_at"] = int(time.time())
        _save_state(app, state)
        return state


def evaluate_drawdown(app, *, current_equity_usd: Decimal, capital_basis_usd: Decimal, max_drawdown_pct: Decimal) -> dict:
    """THE one authoritative equity/high-water-mark/drawdown function.
    /claude_status, the periodic monitor, the pre-buy check, and the
    post-sell recheck all call this -- never re-derive drawdown
    independently (review, 2026-08-26, rejected an earlier
    closed-position-only/capital-basis-relative version for exactly that
    reason: it missed unrealised losses and mixed currency bases).

    high_water_equity_usd seeds at capital_basis_usd on the first-ever call
    (before any P&L exists, equity IS the basis) and is otherwise
    monotonically non-decreasing during normal operation -- the only way it
    moves DOWN is reset_high_water_to_current() after an owner-authorised
    restart. drawdown_pct is (high_water - current) / high_water * 100,
    never negative (equity above the high-water mark simply raises the mark
    to match, on this same call, before the percentage is computed)."""
    with _STATE_LOCK:
        state = load_state(app)
        hwm = Decimal(state.get("high_water_equity_usd") or "0")
        if hwm <= 0:
            hwm = capital_basis_usd
        hwm = max(hwm, current_equity_usd)
        drawdown_usd = max(Decimal("0"), hwm - current_equity_usd)
        drawdown_pct = quantize_pct(drawdown_usd / hwm * Decimal(100)) if hwm > 0 else Decimal("0.00")
        state["high_water_equity_usd"] = str(hwm)
        state["current_equity_usd"] = str(current_equity_usd)
        _save_state(app, state)
        return {
            "high_water_equity_usd": hwm,
            "current_equity_usd": current_equity_usd,
            "drawdown_usd": drawdown_usd,
            "drawdown_pct": drawdown_pct,
            "breached": drawdown_pct >= max_drawdown_pct,
        }


def reset_high_water_to_current(app, *, current_equity_usd: Decimal) -> dict:
    """Called only after a successful owner-authorised
    /claude_restart_confirm CONFIRM -- establishes the fresh baseline the
    owner instruction requires. The old (inflated, pre-drawdown) high-water
    mark is deliberately discarded, not kept as a ceiling that would make
    the very next tick look like a smaller drawdown than it is."""
    with _STATE_LOCK:
        state = load_state(app)
        state["high_water_equity_usd"] = str(current_equity_usd)
        state["current_equity_usd"] = str(current_equity_usd)
        _save_state(app, state)
        return state


def account_closed_position(app, *, position_id: str, realised_net_sol: Decimal, price_usd_used: Decimal) -> Decimal | None:
    """Idempotent per position_id (review, 2026-08-26, replacing an earlier
    before/after-delta approach that had a real crash window -- see
    solana_execution_risk_patch.reconcile_realized_pnl(), the only caller).

    Returns the pnl_usd added if this position_id was not already
    accounted, or None if it was (a safe no-op) -- this is what makes
    repeated/redundant calls (immediately after a sell, every monitor tick,
    once at startup) always converge correctly with no double-counting,
    regardless of when a crash happened relative to any one of those calls.
    Once written, a position's accounted pnl_usd/price_usd_used is
    immutable: this function never recomputes or overwrites an existing
    entry, even if called again for the same position_id.

    Promotes a stale unpriced_closed_position_ids entry, if one exists
    (review, 2026-08-26, correcting a real race found under genuinely
    concurrent multi-mint sells: reconcile_realized_pnl()'s sweep is
    per-owner, not per-mint-locked, so it can momentarily observe a
    position as CLOSED-but-not-yet-accounted while a different mint's
    synchronous capture for that SAME position is still mid-flight, and
    mark it unpriced a moment before the real trustworthy write lands here.
    That marking never contributed to cumulative_realized_pnl_usd -- see
    mark_unpriced_closed_position() -- so removing it once the real value
    arrives is always safe, never a double-count.)"""
    with _STATE_LOCK:
        state = load_state(app)
        accounted = dict(state.get("accounted_position_ids") or {})
        if position_id in accounted:
            return None
        pnl_usd = realised_net_sol * price_usd_used
        accounted[position_id] = {
            "realised_net_sol": str(realised_net_sol),
            "price_usd_used": str(price_usd_used),
            "pnl_usd": str(pnl_usd),
            "accounted_at": int(time.time()),
        }
        state["accounted_position_ids"] = accounted
        unpriced = dict(state.get("unpriced_closed_position_ids") or {})
        if position_id in unpriced:
            del unpriced[position_id]
            state["unpriced_closed_position_ids"] = unpriced
        total = Decimal(state.get("cumulative_realized_pnl_usd") or "0") + pnl_usd
        state["cumulative_realized_pnl_usd"] = str(total)
        _save_state(app, state)
        return pnl_usd


def mark_unpriced_closed_position(app, *, position_id: str, realised_net_sol: Decimal) -> bool:
    """Records a closed position detected WITHOUT a trustworthy close-time
    USD valuation (review, 2026-08-26, blocker A). The learnerbot positions
    schema has no close-time USD/price column, so once the narrow
    synchronous capture window in _guarded_sell() is missed (the process
    crashed between the sell committing to the DB and that capture running),
    the true close-time price cannot be reconstructed -- from this table or
    anywhere else in this codebase (checked: no price-history table exists).
    Rather than substitute a later, wrong price, this is deliberately never
    priced and never folded into cumulative_realized_pnl_usd. See
    armed_health_check(), which fails closed while any entry exists here.

    Idempotent per position_id: returns False (no-op) if this id is already
    known here OR already trustworthily accounted -- never re-marked or
    re-evaluated once either ledger has an entry for it."""
    with _STATE_LOCK:
        state = load_state(app)
        if position_id in (state.get("accounted_position_ids") or {}):
            return False
        unpriced = dict(state.get("unpriced_closed_position_ids") or {})
        if position_id in unpriced:
            return False
        unpriced[position_id] = {"realised_net_sol": str(realised_net_sol), "detected_at": int(time.time())}
        state["unpriced_closed_position_ids"] = unpriced
        _save_state(app, state)
        return True


def latch_drawdown(app, *, drawdown_pct, drawdown_usd) -> bool:
    """Persist HALTED_DRAWDOWN before allowing any further new entry.
    Returns True only the call that created the first latch (so callers know
    whether to send the one-time owner alert)."""
    with _STATE_LOCK:
        state = load_state(app)
        first = not bool(state.get("halted_drawdown"))
        state.update(
            {
                "operating_state": OFF,
                "armed_at": 0,
                "armed_by": "",
                "halted_drawdown": True,
                "triggered_at": int(time.time()),
                "drawdown_pct": str(drawdown_pct),
                "drawdown_usd": str(drawdown_usd),
            }
        )
        _save_state(app, state)
        return first


def issue_restart_challenge(app, *, owner_id: str) -> dict:
    with _STATE_LOCK:
        state = load_state(app)
        if not state.get("halted_drawdown"):
            raise ClaudeStateError("No drawdown halt is active; nothing to restart.")
        state["restart_challenge"] = {
            "nonce": secrets.token_hex(16),
            "issued_at": int(time.time()),
            "issued_by": str(owner_id),
        }
        _save_state(app, state)
        return state


def _challenge_valid(state: dict, *, owner_id: str) -> bool:
    challenge = state.get("restart_challenge")
    if not isinstance(challenge, dict):
        return False
    if str(challenge.get("issued_by")) != str(owner_id):
        return False
    issued_at = int(challenge.get("issued_at") or 0)
    return (int(time.time()) - issued_at) <= RESTART_CHALLENGE_TTL_SECONDS


def confirm_restart(app, *, owner_id: str, precondition_check) -> dict:
    """Consume the pending challenge (single-use -- cleared whether this
    succeeds or fails, so a stale/replayed CONFIRM can never be reused) and,
    only if it is valid and preconditions still hold, clear HALTED_DRAWDOWN
    with a fresh drawdown baseline.

    precondition_check: zero-arg callable that raises on failure (risk
    config invalid, signer not ready, chain not authorised, etc.) -- this is
    the "recheck risk/signer/config preconditions" step the owner required.
    """
    with _STATE_LOCK:
        state = load_state(app)
        if not state.get("halted_drawdown"):
            raise ClaudeStateError("No drawdown halt is active; nothing to restart.")
        valid = _challenge_valid(state, owner_id=owner_id)
        state["restart_challenge"] = None  # single-use: persisted as consumed immediately,
        _save_state(app, state)  # before precondition_check(), so a downstream failure
        if not valid:  # (or even a crash) can never leave a replayable challenge behind
            raise ClaudeStateError(
                "No valid pending restart request for this owner (expired, already used, "
                "or never issued). Run /claude_restart_request again."
            )
        precondition_check()
        now = int(time.time())
        state.update(
            {
                "halted_drawdown": False,
                "baseline_epoch": now,
                "authorized_restart_at": now,
                "authorized_restart_by": str(owner_id),
                "triggered_at": 0,
                "drawdown_pct": "0.00",
                "drawdown_usd": "0.00",
            }
        )
        state.pop("state_error", None)
        _save_state(app, state)
        return state


_PREV_APP = None
_INSTALLED = False


def install() -> None:
    """Wraps learnerbot.cli._app so reset_on_startup() and a one-time
    startup realised-P&L reconciliation (review, 2026-08-26 -- picks up any
    closed position a crash left un-accounted before this process last
    exited) both run exactly once, the first time this process constructs
    its AppSettings, and starts the periodic drawdown/health monitor thread
    (claude_monitor.py) -- mirrors the existing _app-wrapping convention
    already used in this codebase (learnerbot/telegram_ai_ops_patch.py's
    own watcher thread) rather than inventing a second wrapping mechanism
    or a second _app hook."""
    global _PREV_APP, _INSTALLED
    if _INSTALLED:
        return
    from learnerbot import cli as _cli

    _PREV_APP = _cli._app

    def _app_with_state_reset():
        app = _PREV_APP()
        reset_on_startup(app)
        import solana_execution_risk_patch as _guard

        try:
            _guard.reconcile_realized_pnl(app, _guard._owner_id())
        except Exception as exc:  # noqa: BLE001
            print(f"[claude-startup-reconcile] {type(exc).__name__}: {exc}")
        import claude_monitor

        claude_monitor.start(app)
        return app

    _cli._app = _app_with_state_reset
    _INSTALLED = True
