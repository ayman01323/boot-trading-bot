from __future__ import annotations

from contextlib import closing
from contextvars import ContextVar
from threading import Lock

from . import solana_emergency_liquidity_unwind_patch as _unwind
from . import solana_execution_validation_patch as _validation
from . import solana_exit_circuit_breaker_patch as _exit
from . import solana_live_executor as _exec
from . import solana_position_wallet_binding_patch as _binding
from . import solana_sibot as _sol


# Explicit-manual-only escape hatch for a stale exit circuit. The durable circuit
# row is deliberately left BLOCKED while proof is collected and while the manual
# retry runs. Automatic monitor threads therefore continue through the ordinary
# duplicate-SELL guard and cannot inherit this permission.
_BASE_FORCE_CLOSE = _unwind.force_close_live_position
_BASE_GUARDED_CLOSE = _unwind._BASE_CLOSE
_MANUAL_RETRY_POSITION = ContextVar("solana_manual_force_retry_position", default="")
_INFLIGHT_LOCK = Lock()
_INFLIGHT_POSITIONS: set[str] = set()
_BLOCKED = {"RECONCILING", "LANDED_INVALID"}


def _load_owned_open_position(app, tid, position_id: str) -> dict:
    with closing(_sol.connect(app)) as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE position_id=?",
            (str(position_id),),
        ).fetchone()
    if not row:
        raise ValueError("Unknown Solana position")
    position = dict(row)
    if str(position.get("telegram_id")) != str(tid):
        raise ValueError("This position does not belong to this account")
    if str(position.get("status") or "").upper() != "OPEN":
        raise ValueError("Position is not open")
    return position


def _enter_manual_retry(position_id: str) -> None:
    with _INFLIGHT_LOCK:
        if position_id in _INFLIGHT_POSITIONS:
            raise ValueError(
                "A manual force-exit attempt is already running for this position"
            )
        _INFLIGHT_POSITIONS.add(position_id)


def _leave_manual_retry(position_id: str) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT_POSITIONS.discard(position_id)


def _manual_context_close(app, tid, position, fraction, reason):
    """Bypass the stale circuit only inside the proved explicit-manual context.

    Outside that context this is a byte-for-byte behavioural delegation to the
    existing guarded close. Inside it we call the same inner close that the guard
    would have called, and recreate the same circuit states for any NEW ambiguous
    or landed-invalid transaction. Thus the bypass cannot suppress post-broadcast
    reconciliation protection.
    """
    pid = str((position or {}).get("position_id") or "")
    permitted = str(_MANUAL_RETRY_POSITION.get() or "")
    if not pid or permitted != pid:
        return _BASE_GUARDED_CLOSE(app, tid, position, fraction, reason)

    try:
        return _exit._PREV_CLOSE(app, tid, position, fraction, reason)
    except _validation.SolanaLiveReconciliationPending as exc:
        _exit._open_circuit(
            app,
            tid,
            position,
            exc,
            fraction,
            reason,
            "RECONCILING",
        )
        raise
    except _exec.SolanaLivePostExecutionError as exc:
        _exit._open_circuit(
            app,
            tid,
            position,
            exc,
            fraction,
            reason,
            "LANDED_INVALID",
        )
        raise


def _reconcile_first_force_close(app, tid, position_id: str) -> dict:
    """Run an explicit force exit only after proving an old circuit cannot double-sell.

    If the position has no blocked circuit, behaviour is unchanged. If a circuit
    is open, its recorded transaction must first be visible on chain. A successful
    transaction that reduced the position-token balance is reconciled and NEVER
    followed by another SELL in this invocation. A retry is permitted only when
    the recorded transaction either failed atomically or succeeded with no
    position-token decrease.
    """
    pid = str(position_id or "").strip()
    if not pid:
        raise ValueError("Position ID is required")

    _enter_manual_retry(pid)
    try:
        position = _load_owned_open_position(app, tid, pid)
        circuit = _exit.circuit_row(app, pid)
        status = str((circuit or {}).get("status") or "").upper()

        # Ordinary/manual behaviour remains exactly as before unless an unresolved
        # duplicate-SELL circuit is what blocks the requested position.
        if status not in _BLOCKED:
            return dict(_BASE_FORCE_CLOSE(app, tid, pid) or {})

        signature = str((circuit or {}).get("tx_signature") or "").strip()
        if not signature:
            raise ValueError(
                "Manual force exit refused: the open exit circuit has no transaction "
                "signature, so a previous SELL cannot be proved safe to retry"
            )

        try:
            executor, _actual = _binding._resolve_executor(app, tid, position)
        except Exception as exc:
            raise ValueError(
                "Manual force exit refused: the entry wallet could not be resolved "
                f"for on-chain reconciliation ({type(exc).__name__}: {exc})"
            ) from exc

        evidence = dict(
            _exit._chain_sell_evidence(
                app,
                executor,
                signature,
                str(position.get("mint") or ""),
            )
            or {}
        )
        if not evidence.get("visible"):
            raise ValueError(
                "Manual force exit refused: the previous SELL transaction is not "
                "conclusively visible on chain yet; no second SELL was broadcast"
            )

        tx_ok = evidence.get("tx_ok")
        if tx_ok is True:
            token_delta = int(evidence.get("token_delta_raw") or 0)
            if token_delta < 0:
                # This is positive evidence that tokens already left the wallet.
                # Keep the durable circuit blocked while the existing transaction
                # is finalised; never chain a second SELL behind it in this call.
                _exit._mark_circuit(
                    app,
                    pid,
                    "RECONCILING",
                    "manual force-exit preflight proved prior SELL reduced the position-token balance",
                )
                try:
                    _exit.reconcile_pending_exit_circuits(app, limit=10)
                except Exception:
                    # Reconciliation remains fail-closed. The important invariant is
                    # that no second transaction is broadcast from this path.
                    pass

                with closing(_sol.connect(app)) as conn:
                    refreshed = conn.execute(
                        "SELECT status,exit_signature FROM positions WHERE position_id=?",
                        (pid,),
                    ).fetchone()
                if refreshed and str(refreshed["status"] or "").upper() == "CLOSED":
                    return {
                        "closed": True,
                        "position_id": pid,
                        "signature": str(refreshed["exit_signature"] or signature),
                        "reconciled_existing_exit": True,
                        "manual_retry_broadcast": False,
                    }
                raise ValueError(
                    "Previous SELL is confirmed on chain and reduced this token balance. "
                    "It was sent to reconciliation and no second SELL was broadcast; "
                    "retry only after that reconciliation has completed"
                )

            # Successful transaction, but no position-token decrease: safe proof
            # that the stale recorded transaction did not sell this position.
            proof = "prior transaction confirmed with no position-token decrease"
        elif tx_ok is False:
            # A failed Solana transaction is atomic: fees may land, but token-state
            # changes do not. This is conclusive no-sale evidence for the retry.
            proof = "prior transaction confirmed failed on chain"
        else:
            raise ValueError(
                "Manual force exit refused: previous transaction state is ambiguous; "
                "no second SELL was broadcast"
            )

        # Scope the bypass to this context and exact position only. The DB circuit
        # remains RECONCILING/LANDED_INVALID throughout, so other threads and all
        # automatic exits still see the ordinary hard block.
        token = _MANUAL_RETRY_POSITION.set(pid)
        try:
            result = dict(_BASE_FORCE_CLOSE(app, tid, pid) or {})
        finally:
            _MANUAL_RETRY_POSITION.reset(token)

        # A normally returned inner close has already reconciled/accounted its own
        # transaction. The stale circuit can therefore be retired. Any NEW
        # post-broadcast ambiguity raises instead and is caught by
        # _manual_context_close above, which replaces the circuit with the new
        # signature and leaves it blocked.
        _exit._mark_circuit(
            app,
            pid,
            "RECONCILED",
            "explicit manual force-exit retry completed after chain proof: " + proof,
        )
        result["manual_force_reconcile_first"] = True
        result["manual_retry_prior_signature"] = signature
        result["manual_retry_prior_proof"] = proof
        return result
    finally:
        _leave_manual_retry(pid)


def install() -> None:
    if getattr(_sol, "_manual_force_exit_reconcile_installed", False):
        return

    # These are the ONLY two hooks changed. The exported automatic
    # solana_exit_circuit_breaker.close_live_guarded remains untouched.
    _unwind._BASE_CLOSE = _manual_context_close
    _unwind.force_close_live_position = _reconcile_first_force_close
    _sol._manual_force_exit_reconcile_installed = True

    if _unwind._BASE_CLOSE is not _manual_context_close:
        raise RuntimeError("manual force-exit reconcile close hook displaced")
    if _unwind.force_close_live_position is not _reconcile_first_force_close:
        raise RuntimeError("manual force-exit reconcile command hook displaced")

    print(
        "[solana-manual-force-exit-reconcile] reconcile_first=true "
        "manual_context_only=true automatic_exit_guard_unchanged=true reversible_patch=true"
    )


install()
