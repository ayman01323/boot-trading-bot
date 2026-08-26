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
import threading
import time
from pathlib import Path

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
    """Wraps learnerbot.cli._app so reset_on_startup() runs exactly once,
    the first time this process constructs its AppSettings -- mirrors the
    existing _app-wrapping convention already used in this codebase (see
    the removed telegram_claude_smoke_patch.py) rather than requiring every
    caller to remember to call it explicitly."""
    global _PREV_APP, _INSTALLED
    if _INSTALLED:
        return
    from learnerbot import cli as _cli

    _PREV_APP = _cli._app

    def _app_with_state_reset():
        app = _PREV_APP()
        reset_on_startup(app)
        return app

    _cli._app = _app_with_state_reset
    _INSTALLED = True
