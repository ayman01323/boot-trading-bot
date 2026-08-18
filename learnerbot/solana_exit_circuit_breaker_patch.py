from __future__ import annotations

import time
from contextlib import closing

from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_refundable_rent_accounting_patch as _rent
from . import solana_sibot as _sol
from .user_registry import set_user_setting

_PREV_CLOSE = _rent._close_live_rent_aware

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS live_exit_circuit(
  position_id TEXT PRIMARY KEY,
  telegram_id TEXT NOT NULL,
  status TEXT NOT NULL,
  tx_signature TEXT,
  error TEXT,
  opened_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
"""


def _ensure(conn):
    conn.executescript(_SCHEMA)


def circuit_row(app, position_id):
    with closing(_sol.connect(app)) as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM live_exit_circuit WHERE position_id=?",
            (str(position_id),),
        ).fetchone()
        return dict(row) if row else None


def _open_circuit(app, tid, position, exc):
    pid = str(position.get("position_id") or "")
    sig = str(getattr(exc, "signature", "") or "")
    result = dict(getattr(exc, "result", {}) or {})
    if not sig:
        sig = str(result.get("signature") or "")
    now = int(time.time())
    message = str(exc)[:1200]
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        _ensure(conn)
        conn.execute(
            """INSERT INTO live_exit_circuit(position_id,telegram_id,status,tx_signature,error,opened_at,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(position_id) DO UPDATE SET
                 status=excluded.status,tx_signature=excluded.tx_signature,error=excluded.error,updated_at=excluded.updated_at""",
            (pid, str(tid), "LANDED_INVALID", sig, message, now, now),
        )
        conn.execute(
            """UPDATE positions
               SET leader_exit_pending=1,
                   exit_reason=?,
                   updated_at=?
               WHERE position_id=? AND status='OPEN'""",
            ("EXIT_CIRCUIT_LANDED_INVALID: " + message[:450], now, pid),
        )
        conn.commit()

    set_user_setting(
        app.csv_dir,
        str(tid),
        "solana_live_enabled",
        "false",
        chain_id=_sol.SOLANA_CHAIN_ID,
        description="Disabled after landed-invalid monitored Solana exit",
    )
    _live._notify(
        app,
        tid,
        "🛑 <b>Solana LIVE exit circuit opened</b>\n"
        f"Position: <code>{pid}</code>\n"
        + (f"TX: <code>{sig}</code>\n" if sig else "")
        + f"Fault: <code>{message[:500]}</code>\n"
        "Automatic retries for this position are blocked. Solana LIVE has been disabled for this account until the position is reconciled.",
    )


def close_live_guarded(app, tid, position, fraction, reason):
    pid = str(position.get("position_id") or "")
    row = circuit_row(app, pid) if pid else None
    if row and str(row.get("status") or "").upper() == "LANDED_INVALID":
        raise _exec.SolanaLiveError(
            "Solana exit circuit is open for this position after a landed-invalid execution; automatic retry blocked"
        )
    try:
        return _PREV_CLOSE(app, tid, position, fraction, reason)
    except _exec.SolanaLivePostExecutionError as exc:
        _open_circuit(app, tid, position, exc)
        raise


def install():
    # Keep the rent-accounting implementation itself immutable. The circuit is the
    # outer public guard used by both monitored LIVE and wallet-bound exit paths.
    _binding._close_bound_live = close_live_guarded
    _live._close_live = close_live_guarded
    print(
        "[solana-exit-circuit] first_landed_invalid_disables_live=true "
        "position_retry_block=true rent_accounting_inner=true"
    )


install()
