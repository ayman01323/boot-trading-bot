from __future__ import annotations

import json
import time
from contextlib import closing
from decimal import Decimal

from . import solana_execution_validation_patch as _validation
from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_refundable_rent_accounting_patch as _rent
from . import solana_sibot as _sol
from .user_registry import set_user_setting

_PREV_CLOSE = _rent._close_live_rent_aware
_MONITOR_INNER = None

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS live_exit_circuit(
  position_id TEXT PRIMARY KEY,
  telegram_id TEXT NOT NULL,
  status TEXT NOT NULL,
  tx_signature TEXT,
  error TEXT,
  payload_json TEXT,
  fraction TEXT,
  close_reason TEXT,
  sell_raw TEXT,
  opened_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
"""


def _ensure(conn):
    conn.executescript(_SCHEMA)
    existing = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(live_exit_circuit)").fetchall()
    }
    additions = {
        "payload_json": "TEXT",
        "fraction": "TEXT",
        "close_reason": "TEXT",
        "sell_raw": "TEXT",
    }
    for name, sql_type in additions.items():
        if name not in existing:
            conn.execute(
                f"ALTER TABLE live_exit_circuit ADD COLUMN {name} {sql_type}"
            )


def circuit_row(app, position_id):
    with closing(_sol.connect(app)) as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM live_exit_circuit WHERE position_id=?",
            (str(position_id),),
        ).fetchone()
        return dict(row) if row else None


def _payload(exc) -> dict:
    result = dict(getattr(exc, "result", {}) or {})
    try:
        # Round trip through JSON to ensure SQLite receives bounded plain data.
        return json.loads(json.dumps(result, default=str))
    except Exception:
        return {
            "signature": str(result.get("signature") or ""),
            "requested_sell_raw": str(result.get("requested_sell_raw") or ""),
        }


def _open_circuit(app, tid, position, exc, fraction, reason, status):
    pid = str(position.get("position_id") or "")
    result = _payload(exc)
    sig = str(getattr(exc, "signature", "") or result.get("signature") or "")
    now = int(time.time())
    message = str(exc)[:1200]
    sell_raw = str(
        result.get("requested_sell_raw")
        or result.get("input_token_delta_raw")
        or ""
    )
    payload_json = json.dumps(result, separators=(",", ":"), default=str)

    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _ensure(conn)
        conn.execute(
            """INSERT INTO live_exit_circuit(
                 position_id,telegram_id,status,tx_signature,error,payload_json,
                 fraction,close_reason,sell_raw,opened_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(position_id) DO UPDATE SET
                 status=excluded.status,
                 tx_signature=excluded.tx_signature,
                 error=excluded.error,
                 payload_json=excluded.payload_json,
                 fraction=excluded.fraction,
                 close_reason=excluded.close_reason,
                 sell_raw=excluded.sell_raw,
                 updated_at=excluded.updated_at""",
            (
                pid,
                str(tid),
                str(status),
                sig,
                message,
                payload_json,
                str(fraction),
                str(reason),
                sell_raw,
                now,
                now,
            ),
        )
        conn.execute(
            """UPDATE positions
               SET leader_exit_pending=1,
                   exit_reason=?,
                   updated_at=?
               WHERE position_id=? AND status='OPEN'""",
            (
                f"EXIT_CIRCUIT_{str(status).upper()}: " + message[:450],
                now,
                pid,
            ),
        )
        conn.commit()

    set_user_setting(
        app.csv_dir,
        str(tid),
        "solana_live_enabled",
        "false",
        chain_id=_sol.SOLANA_CHAIN_ID,
        description=(
            "Disabled while a Jupiter-success Solana exit is being reconciled"
            if str(status).upper() == "RECONCILING"
            else "Disabled after landed-invalid monitored Solana exit"
        ),
    )

    if str(status).upper() == "RECONCILING":
        _live._notify(
            app,
            tid,
            "⏳ <b>Solana LIVE exit reconciliation started</b>\n"
            f"Position: <code>{pid}</code>\n"
            + (f"TX: <code>{sig}</code>\n" if sig else "")
            + f"State: <code>{message[:500]}</code>\n"
            "No second SELL will be broadcast. New Solana LIVE entries are disabled while the exact transaction is checked on chain.",
        )
    else:
        _live._notify(
            app,
            tid,
            "🛑 <b>Solana LIVE exit circuit opened</b>\n"
            f"Position: <code>{pid}</code>\n"
            + (f"TX: <code>{sig}</code>\n" if sig else "")
            + f"Fault: <code>{message[:500]}</code>\n"
            "Automatic retries for this position are blocked. Solana LIVE has been disabled for this account.",
        )


def _chain_sell_evidence(app, executor, signature, mint):
    try:
        tx = _sol._rpc(
            app,
            "getTransaction",
            [
                str(signature),
                {
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                    "encoding": "jsonParsed",
                },
            ],
        )
    except Exception as exc:
        return {
            "visible": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not tx:
        return {"visible": False, "error": "transaction not yet visible"}

    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return {
            "visible": True,
            "tx_ok": False,
            "error": str(meta.get("err")),
        }

    deltas, _pre, _post, _decimals = _sol._token_state(tx, executor.address)
    token_delta = int(deltas.get(str(mint), 0))
    wallet_delta = None
    try:
        wallet_delta = int(
            (
                _sol._sol_delta(tx, executor.address)
                * Decimal(1_000_000_000)
            ).to_integral_value()
        )
    except Exception:
        wallet_delta = None

    return {
        "visible": True,
        "tx_ok": True,
        "token_delta_raw": token_delta,
        "wallet_delta_lamports": wallet_delta,
        "slot": int(tx.get("slot") or 0),
    }


def _mark_circuit(app, position_id, status, error=""):
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE live_exit_circuit SET status=?,error=?,updated_at=? WHERE position_id=?",
            (
                str(status),
                str(error or "")[:1200],
                int(time.time()),
                str(position_id),
            ),
        )
        conn.commit()


def reconcile_pending_exit_circuits(app, limit=10):
    """Reconcile, but never rebroadcast, ambiguous Jupiter-success SELLs."""
    with closing(_sol.connect(app)) as conn:
        _ensure(conn)
        rows = [
            dict(r)
            for r in conn.execute(
                """SELECT * FROM live_exit_circuit
                   WHERE status='RECONCILING'
                      OR (
                           status='LANDED_INVALID'
                           AND error LIKE '%SELL produced no input-token balance decrease%'
                         )
                   ORDER BY updated_at
                   LIMIT ?""",
                (max(1, min(50, int(limit))),),
            ).fetchall()
        ]

    recovered = 0
    for row in rows:
        pid = str(row.get("position_id") or "")
        tid = str(row.get("telegram_id") or "")
        sig = str(row.get("tx_signature") or "")
        if not pid or not sig:
            continue

        with closing(_sol.connect(app)) as conn:
            pos_row = conn.execute(
                "SELECT * FROM positions WHERE position_id=?",
                (pid,),
            ).fetchone()
        if not pos_row:
            _mark_circuit(app, pid, "ORPHANED", "position row no longer exists")
            continue
        position = dict(pos_row)

        # If a previous recovery cycle finalized the same signature and crashed
        # before updating the circuit row, complete the state transition only.
        if str(position.get("exit_signature") or "") == sig:
            _mark_circuit(app, pid, "RECONCILED", "already finalized")
            recovered += 1
            continue

        try:
            executor, _actual = _binding._resolve_executor(app, tid, position)
        except Exception as exc:
            _mark_circuit(
                app,
                pid,
                "RECONCILING",
                f"wallet resolution pending: {type(exc).__name__}: {exc}",
            )
            continue

        evidence = _chain_sell_evidence(
            app, executor, sig, str(position.get("mint") or "")
        )
        if not evidence.get("visible"):
            # Keep the state pending. Crucially, no SELL is sent from here.
            continue
        if not evidence.get("tx_ok"):
            _mark_circuit(
                app,
                pid,
                "LANDED_INVALID",
                "on-chain transaction failed: " + str(evidence.get("error") or ""),
            )
            _live._notify(
                app,
                tid,
                "🛑 <b>Solana exit reconciliation found an on-chain failure</b>\n"
                f"Position: <code>{pid}</code>\n"
                f"TX: <code>{sig}</code>\n"
                "The position was not marked closed and no automatic retry was broadcast.",
            )
            continue

        token_delta = int(evidence.get("token_delta_raw") or 0)
        executed_sell_raw = -token_delta
        if executed_sell_raw <= 0:
            _mark_circuit(
                app,
                pid,
                "LANDED_INVALID",
                "confirmed transaction contains no negative position-token delta",
            )
            continue

        payload = {}
        try:
            payload = json.loads(str(row.get("payload_json") or "") or "{}")
        except Exception:
            payload = {}
        payload = dict(payload or {})
        payload["signature"] = sig
        payload["chain_tx_visible"] = True
        payload["chain_tx_reconciled"] = True
        payload["chain_tx_slot"] = int(evidence.get("slot") or 0)
        payload["chain_token_delta_raw"] = token_delta
        payload["input_token_delta_raw"] = executed_sell_raw
        payload["input_token_reconciliation_source"] = "getTransaction"
        payload["recovered_from_exit_circuit"] = True
        if evidence.get("wallet_delta_lamports") is not None:
            payload["wallet_delta_lamports"] = int(
                evidence["wallet_delta_lamports"]
            )
            payload["wallet_balance_reconciled"] = True
            payload["wallet_reconciliation_source"] = "getTransaction"

        old_raw = max(1, _sol._int(position.get("token_amount_raw"), 0))
        stored_sell_raw = _sol._int(
            row.get("sell_raw")
            or payload.get("requested_sell_raw")
            or executed_sell_raw,
            executed_sell_raw,
        )
        # Transaction metadata is authoritative if it disagrees with a stale or
        # incomplete circuit payload.
        sell_raw = min(old_raw, max(1, executed_sell_raw or stored_sell_raw))

        try:
            fraction = Decimal(str(row.get("fraction") or "0"))
        except Exception:
            fraction = Decimal(0)
        if fraction <= 0:
            fraction = min(
                Decimal(1),
                Decimal(sell_raw) / Decimal(old_raw),
            )
        if sell_raw >= max(1, int(old_raw * 0.999)):
            fraction = Decimal(1)

        close_reason = str(row.get("close_reason") or "").strip()
        if not close_reason:
            close_reason = "SOLANA_EXIT_RECONCILED_AFTER_RPC_LAG"

        try:
            _rent.finalize_reconciled_live_sell(
                app,
                tid,
                position,
                fraction,
                close_reason,
                payload,
                sell_raw,
            )
        except Exception as exc:
            _mark_circuit(
                app,
                pid,
                "RECONCILING",
                f"chain proved SELL but accounting finalization failed: {type(exc).__name__}: {exc}",
            )
            continue

        _mark_circuit(
            app,
            pid,
            "RECONCILED",
            "SELL proved from transaction pre/post balances; no rebroadcast",
        )
        recovered += 1
        _live._notify(
            app,
            tid,
            "✅ <b>Solana exit circuit reconciled</b>\n"
            f"Position: <code>{pid}</code>\n"
            f"TX: <code>{sig}</code>\n"
            f"On-chain token decrease: <code>{sell_raw}</code> raw units.\n"
            "The existing transaction was accounted for without broadcasting a second SELL. Solana LIVE remains disabled until you explicitly re-arm it.",
        )

    return recovered


def close_live_guarded(app, tid, position, fraction, reason):
    pid = str(position.get("position_id") or "")
    row = circuit_row(app, pid) if pid else None
    if row and str(row.get("status") or "").upper() in {
        "LANDED_INVALID",
        "RECONCILING",
    }:
        raise _exec.SolanaLiveError(
            "Solana exit circuit is open for this position; automatic SELL retry blocked pending reconciliation"
        )

    try:
        return _PREV_CLOSE(app, tid, position, fraction, reason)
    except _validation.SolanaLiveReconciliationPending as exc:
        _open_circuit(
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
        _open_circuit(
            app,
            tid,
            position,
            exc,
            fraction,
            reason,
            "LANDED_INVALID",
        )
        raise


def _monitor_with_exit_reconciliation(app):
    # This runs even while Solana LIVE is disabled. It only reads the previously
    # submitted signature and finalizes accounting when the chain proves the SELL.
    try:
        reconcile_pending_exit_circuits(app)
    except Exception as exc:
        print("[solana-exit-reconcile]", type(exc).__name__, exc)
    if _MONITOR_INNER is not None:
        return _MONITOR_INNER(app)
    return None


def install():
    global _MONITOR_INNER
    if getattr(_sol, "_exit_circuit_reconciliation_installed", False):
        return

    # Keep the rent-accounting implementation itself immutable. The circuit is the
    # outer public guard used by both monitored LIVE and wallet-bound exit paths.
    _binding._close_bound_live = close_live_guarded
    _live._close_live = close_live_guarded

    _MONITOR_INNER = _sol.monitor_positions
    _sol.monitor_positions = _monitor_with_exit_reconciliation
    _sol.reconcile_pending_exit_circuits = reconcile_pending_exit_circuits
    _sol._exit_circuit_reconciliation_installed = True

    print(
        "[solana-exit-circuit] ambiguous_success=reconcile_only "
        "position_retry_block=true chain_metadata_authoritative=true "
        "legacy_stale_balance_recovery=true"
    )


install()
