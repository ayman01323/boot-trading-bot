"""Periodic, non-trading drawdown/health monitor for the Claude bot.

Added per review (2026-08-26), blockers 2 and 3: a drawdown breach or a
failed ARMED precondition must be caught even when no BUY or SELL is being
attempted -- open-position mark-to-market losses and a signer/config/chain/
kill-switch/composition failure can both occur with the bot sitting idle.

This module MUST ONLY tighten or stop. It has no code path that can arm,
clear a HALTED_DRAWDOWN latch, sign, or broadcast -- it only ever calls
claude_state.latch_drawdown() and claude_state.force_off(), both of which
are one-way-tightening by construction (see claude_state.py). Structural
proof of that boundary lives in tests/test_claude_risk_and_state.py's grep
-based check alongside telegram_control_patch.py's owner-gated-mutator test.

Reuses the exact daemon-thread convention already established in this
codebase (learnerbot/telegram_ai_ops_patch.py's own 60s watcher thread) --
started by wrapping learnerbot.cli._app, same hook claude_state.py already
uses for reset_on_startup(), so there is exactly one _app wrapper, not two.
"""

from __future__ import annotations

import threading
import time

CHECK_INTERVAL_SECONDS = 60

_THREAD_STARTED = False
_LOCK = threading.RLock()


def check_once(app) -> None:
    """One monitor tick. Never raises -- callers (the loop below, and tests)
    can call this directly without needing a try/except of their own."""
    import claude_state
    import risk_engine_guard
    import solana_execution_risk_patch as guard

    owner_id = guard._owner_id()
    if not owner_id:
        return

    try:
        limits = risk_engine_guard.RiskLimits.load()
    except risk_engine_guard.RiskGuardConfigError:
        # Can't evaluate equity without a valid capital basis. If ARMED
        # despite that, the risk config itself is the health failure.
        state = claude_state.load_state(app)
        if state.get("operating_state") == claude_state.ARMED:
            claude_state.force_off(app, reason="risk config invalid (CLAUDE_CAPITAL_BASIS_USD)")
            guard._send_owner_health_alert(app, reason="risk config invalid (CLAUDE_CAPITAL_BASIS_USD)")
        return

    try:
        open_positions = guard._current_live_open_count(app, owner_id)
        guard._check_and_latch_drawdown(app, owner_id, limits=limits, open_positions=open_positions)
    except Exception as exc:  # noqa: BLE001
        print(f"[claude-monitor] drawdown check failed: {type(exc).__name__}: {exc}")

    try:
        state = claude_state.load_state(app)
        if state.get("operating_state") == claude_state.ARMED:
            reason = guard.armed_health_check(app, owner_id)
            if reason:
                claude_state.force_off(app, reason=reason)
                guard._send_owner_health_alert(app, reason=reason)
    except Exception as exc:  # noqa: BLE001
        print(f"[claude-monitor] health check failed: {type(exc).__name__}: {exc}")


def _loop(app) -> None:
    time.sleep(8)
    while True:
        try:
            check_once(app)
        except Exception as exc:  # noqa: BLE001
            print(f"[claude-monitor] {type(exc).__name__}: {exc}")
        time.sleep(CHECK_INTERVAL_SECONDS)


def start(app) -> None:
    global _THREAD_STARTED
    with _LOCK:
        if _THREAD_STARTED:
            return
        if not getattr(app, "telegram_bot_token", ""):
            return
        thread = threading.Thread(target=_loop, args=(app,), name="claude-drawdown-health-monitor", daemon=True)
        thread.start()
        _THREAD_STARTED = True
        print(f"[claude-monitor] started interval={CHECK_INTERVAL_SECONDS}s")
