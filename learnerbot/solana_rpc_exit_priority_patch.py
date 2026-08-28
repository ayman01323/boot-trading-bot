from __future__ import annotations

"""Give risk-reducing Solana exits priority over discovery/scanning RPC traffic.

This is an isolated learner overlay. It does not bypass provider cooldowns,
Retry-After, auth quarantine, simulation, liquidity, signing, validation, or the
exit circuit. It only makes non-critical RPC callers yield while a SELL or
reconciliation is active so scanners cannot win the endpoint race as cooldowns
expire.
"""

import os
import threading
import time
from contextlib import contextmanager

from . import solana_exit_circuit_breaker_patch as _circuit
from . import solana_live_patch as _live
from . import solana_rpc_failover_patch as _failover
from . import solana_sibot as _sol

_PREV_RPC = _sol._rpc
_PREV_CLOSE_LIVE = _live._close_live
_PREV_CHAIN_EVIDENCE = _circuit._chain_sell_evidence

_LOCK = threading.Lock()
_LOCAL = threading.local()
_PRIORITY_UNTIL = 0.0


def _window_seconds() -> float:
    try:
        value = float(os.getenv("SOLANA_RPC_EXIT_PRIORITY_SECONDS", "45"))
    except Exception:
        value = 45.0
    return max(10.0, min(120.0, value))


def _activate_window() -> None:
    global _PRIORITY_UNTIL
    until = time.monotonic() + _window_seconds()
    with _LOCK:
        if until > _PRIORITY_UNTIL:
            _PRIORITY_UNTIL = until


def _priority_active() -> bool:
    with _LOCK:
        return time.monotonic() < _PRIORITY_UNTIL


@contextmanager
def risk_reducing_rpc_priority():
    """Mark the current call chain as critical and suppress scanner contention."""
    previous = bool(getattr(_LOCAL, "critical", False))
    _LOCAL.critical = True
    _activate_window()
    try:
        yield
    finally:
        _LOCAL.critical = previous


def rpc_with_exit_priority(app, method: str, params: list):
    critical = bool(getattr(_LOCAL, "critical", False))
    if not critical and _priority_active():
        # Fast, local rejection: discovery/leader scanning does not touch any
        # provider while capital-reducing work is waiting. Existing workers already
        # treat SolanaRpcEndpointError as transient and retry later.
        raise _failover.SolanaRpcEndpointError(
            method,
            "yielding to risk-reducing exit priority",
            transient=True,
        )
    return _PREV_RPC(app, method, params)


def close_live_with_rpc_priority(app, tid, position, fraction, reason):
    with risk_reducing_rpc_priority():
        return _PREV_CLOSE_LIVE(app, tid, position, fraction, reason)


def chain_sell_evidence_with_rpc_priority(app, executor, signature, mint):
    with risk_reducing_rpc_priority():
        return _PREV_CHAIN_EVIDENCE(app, executor, signature, mint)


def install() -> None:
    if getattr(_sol, "_rpc_exit_priority_installed", False):
        return
    _sol._rpc = rpc_with_exit_priority
    _live._close_live = close_live_with_rpc_priority
    _circuit._chain_sell_evidence = chain_sell_evidence_with_rpc_priority
    _sol._rpc_exit_priority_installed = True
    print(
        "[solana-rpc-exit-priority] active=true exits=priority discovery=yield "
        "reconciliation=priority cooldown_bypass=false auth_bypass=false",
        flush=True,
    )


install()
