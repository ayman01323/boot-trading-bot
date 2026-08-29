"""Single-use approval ledger + state machine for the Grok LIVE canary.

Design boundaries (owner + GPT reviewed, 2026-08-29):
- A real-money broadcast is only ever reachable through an APPROVED, unexpired,
  single-use ticket that a worker has atomically claimed to EXECUTING.
- All state transitions are guarded ``WHERE status=<expected>`` inside a
  transaction, so a restarted or concurrent worker cannot double-broadcast.
- The hard cap is an integer-lamports module constant. No config / env / CSV /
  Telegram argument can raise it. A request above it is refused, not clamped.
- Every exit is approval-gated too (owner decision): there is no automatic sell.

This module is pure SQLite state. It never imports the executor, never signs and
never touches the control file. The runner orchestrates; the executor broadcasts.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any

# 0.009 SOL, as integer lamports. Owner selected 0.009 on 2026-08-29 over the
# 0.005 mailbox default; the choice is recorded in .github/ai-mailbox.
HARD_CAP_LAMPORTS = 9_000_000
TARGET_LAMPORTS = 9_000_000
# SOL that must remain after an entry for fees / rent / an emergency exit.
SOL_FEE_RESERVE_LAMPORTS = 20_000_000

KIND_ENTRY = "ENTRY"
KIND_EXIT = "EXIT"

STATUS_PENDING = "PENDING_APPROVAL"
STATUS_APPROVED = "APPROVED"
STATUS_EXECUTING = "EXECUTING"
STATUS_BROADCAST_SUBMITTED = "BROADCAST_SUBMITTED"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_EXPIRED = "EXPIRED"
STATUS_CANCELLED = "CANCELLED"
STATUS_REJECTED_REVALIDATION = "REJECTED_REVALIDATION"
STATUS_SIMULATION_FAILED = "SIMULATION_FAILED"
STATUS_BROADCAST_FAILED = "BROADCAST_FAILED"
STATUS_UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
STATUS_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

NONTERMINAL = (
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_EXECUTING,
    STATUS_BROADCAST_SUBMITTED,
)
TERMINAL = (
    STATUS_CONFIRMED,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
    STATUS_REJECTED_REVALIDATION,
    STATUS_SIMULATION_FAILED,
    STATUS_BROADCAST_FAILED,
    STATUS_UNKNOWN_OUTCOME,
    STATUS_RECONCILIATION_REQUIRED,
)
# Outcomes that mean "a broadcast may have gone out; a human must reconcile
# on-chain before the canary is allowed to act again".
RECONCILE_OUTCOMES = (STATUS_UNKNOWN_OUTCOME, STATUS_RECONCILIATION_REQUIRED)

_DEFAULT_TTL_SECONDS = 90
_MIN_TTL_SECONDS = 30
_MAX_TTL_SECONDS = 300


class CanaryLedgerError(RuntimeError):
    """A request that must not proceed (cap exceeded, duplicate, expired, ...)."""


def approval_ttl_seconds() -> int:
    raw = os.environ.get("GROK_LIVE_CANARY_APPROVAL_TTL_SECONDS", "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_TTL_SECONDS
    return max(_MIN_TTL_SECONDS, min(_MAX_TTL_SECONDS, value))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_canary_approvals(
  approval_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  asset_key TEXT NOT NULL,
  mint TEXT NOT NULL,
  input_micro_usdc INTEGER NOT NULL DEFAULT 0,
  target_lamports INTEGER NOT NULL DEFAULT 0,
  min_out_lamports INTEGER NOT NULL DEFAULT 0,
  slippage_bps INTEGER NOT NULL DEFAULT 0,
  position_approval_id TEXT,
  acquired_lamports INTEGER NOT NULL DEFAULT 0,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_epoch INTEGER NOT NULL,
  expires_epoch INTEGER NOT NULL,
  approved_epoch INTEGER,
  approved_by_user_id TEXT,
  approved_in_chat_id TEXT,
  claimed_epoch INTEGER,
  tx_signature TEXT,
  outcome_detail TEXT,
  updated_epoch INTEGER NOT NULL
);
-- At most one non-terminal ENTRY ticket may exist at a time.
CREATE UNIQUE INDEX IF NOT EXISTS ux_canary_one_active_entry
  ON live_canary_approvals(kind)
  WHERE kind='ENTRY' AND status IN
    ('PENDING_APPROVAL','APPROVED','EXECUTING','BROADCAST_SUBMITTED');
-- At most one non-terminal EXIT ticket per open position.
CREATE UNIQUE INDEX IF NOT EXISTS ux_canary_one_active_exit
  ON live_canary_approvals(position_approval_id)
  WHERE kind='EXIT' AND status IN
    ('PENDING_APPROVAL','APPROVED','EXECUTING','BROADCAST_SUBMITTED');
CREATE INDEX IF NOT EXISTS idx_canary_status ON live_canary_approvals(status);
"""


def ensure_schema(db: sqlite3.Connection) -> None:
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        pass
    db.executescript(_SCHEMA)
    db.commit()


def _row(db: sqlite3.Connection, approval_id: str) -> dict[str, Any] | None:
    cur = db.execute(
        "SELECT * FROM live_canary_approvals WHERE approval_id=?", (str(approval_id),)
    )
    names = [c[0] for c in cur.description]
    row = cur.fetchone()
    return dict(zip(names, row)) if row else None


def _audit(db: sqlite3.Connection, kind: str, approval_id: str, detail: dict[str, Any]) -> None:
    """Best-effort transition audit into the shared events table."""
    try:
        db.execute(
            "INSERT INTO events(ts, kind, asset_key, payload) VALUES(?,?,?,?)",
            (time.time(), kind, str(detail.get("asset_key") or ""), json.dumps({"approval_id": approval_id, **detail}, sort_keys=True)),
        )
    except sqlite3.Error:
        pass


def open_entry_position_count(db: sqlite3.Connection) -> int:
    """CONFIRMED ENTRY tickets with no CONFIRMED EXIT against them."""
    ensure_schema(db)
    row = db.execute(
        """
        SELECT COUNT(*) FROM live_canary_approvals e
        WHERE e.kind='ENTRY' AND e.status='CONFIRMED'
          AND NOT EXISTS (
            SELECT 1 FROM live_canary_approvals x
            WHERE x.kind='EXIT' AND x.status='CONFIRMED'
              AND x.position_approval_id = e.approval_id
          )
        """
    ).fetchone()
    return int(row[0] or 0)


def active_entry_ticket(db: sqlite3.Connection) -> dict[str, Any] | None:
    ensure_schema(db)
    cur = db.execute(
        "SELECT * FROM live_canary_approvals WHERE kind='ENTRY' AND status IN "
        "('PENDING_APPROVAL','APPROVED','EXECUTING','BROADCAST_SUBMITTED') LIMIT 1"
    )
    names = [c[0] for c in cur.description]
    row = cur.fetchone()
    return dict(zip(names, row)) if row else None


def create_pending_entry(
    db: sqlite3.Connection,
    *,
    asset_key: str,
    mint: str,
    input_micro_usdc: int,
    target_lamports: int,
    min_out_lamports: int,
    slippage_bps: int,
    evidence: dict[str, Any],
    now: float | None = None,
    ttl_seconds: int | None = None,
) -> str:
    ensure_schema(db)
    now = int(time.time() if now is None else now)
    target_lamports = int(target_lamports)
    if target_lamports <= 0:
        raise CanaryLedgerError("entry target must be positive")
    if target_lamports > HARD_CAP_LAMPORTS:
        raise CanaryLedgerError(
            f"AMOUNT_EXCEEDS_HARD_CAP target={target_lamports} cap={HARD_CAP_LAMPORTS}"
        )
    if int(input_micro_usdc) <= 0 or int(min_out_lamports) <= 0:
        raise CanaryLedgerError("entry quote invariants missing")
    ttl = int(approval_ttl_seconds() if ttl_seconds is None else ttl_seconds)
    approval_id = uuid.uuid4().hex
    try:
        db.execute("BEGIN IMMEDIATE")
        if open_entry_position_count(db) >= 1:
            db.rollback()
            raise CanaryLedgerError("a LIVE canary position is already open")
        db.execute(
            """INSERT INTO live_canary_approvals(
                 approval_id,kind,asset_key,mint,input_micro_usdc,target_lamports,
                 min_out_lamports,slippage_bps,evidence_json,status,
                 created_epoch,expires_epoch,updated_epoch
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                approval_id, KIND_ENTRY, str(asset_key), str(mint), int(input_micro_usdc),
                target_lamports, int(min_out_lamports), int(slippage_bps),
                json.dumps(dict(evidence or {}), sort_keys=True), STATUS_PENDING,
                now, now + ttl, now,
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        raise CanaryLedgerError("a non-terminal ENTRY approval already exists")
    _audit(db, "CANARY_PENDING", approval_id, {"asset_key": asset_key, "mint": mint, "target_lamports": target_lamports, "expires_epoch": now + ttl})
    return approval_id


def approve_entry(
    db: sqlite3.Connection,
    approval_id: str,
    *,
    user_id: str,
    chat_id: str,
    now: float | None = None,
) -> dict[str, Any]:
    ensure_schema(db)
    now = int(time.time() if now is None else now)
    row = _row(db, approval_id)
    if row is None:
        raise CanaryLedgerError("unknown approval id")
    if row["kind"] != KIND_ENTRY:
        raise CanaryLedgerError("not an entry approval")
    if row["status"] != STATUS_PENDING:
        raise CanaryLedgerError(f"approval is not pending (status={row['status']})")
    if now >= int(row["expires_epoch"]):
        db.execute(
            "UPDATE live_canary_approvals SET status=?,outcome_detail=?,updated_epoch=? "
            "WHERE approval_id=? AND status=?",
            (STATUS_EXPIRED, "expired before approval", now, str(approval_id), STATUS_PENDING),
        )
        db.commit()
        raise CanaryLedgerError("approval has expired")
    cur = db.execute(
        "UPDATE live_canary_approvals SET status=?,approved_epoch=?,approved_by_user_id=?,"
        "approved_in_chat_id=?,updated_epoch=? WHERE approval_id=? AND status=?",
        (STATUS_APPROVED, now, str(user_id), str(chat_id), now, str(approval_id), STATUS_PENDING),
    )
    db.commit()
    if cur.rowcount != 1:
        raise CanaryLedgerError("approval could not be transitioned (already claimed?)")
    _audit(db, "CANARY_APPROVED", approval_id, {"asset_key": row["asset_key"], "approved_by_user_id": str(user_id), "approved_in_chat_id": str(chat_id)})
    return _row(db, approval_id) or {}


def create_approved_exit(
    db: sqlite3.Connection,
    *,
    position_approval_id: str,
    user_id: str,
    chat_id: str,
    now: float | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Owner explicitly requests a sell: the /grokexit CONFIRM is itself the approval."""
    ensure_schema(db)
    now = int(time.time() if now is None else now)
    pos = _row(db, position_approval_id)
    if pos is None or pos["kind"] != KIND_ENTRY:
        raise CanaryLedgerError("unknown open position")
    if pos["status"] != STATUS_CONFIRMED:
        raise CanaryLedgerError(f"position is not a confirmed live entry (status={pos['status']})")
    acquired = int(pos["acquired_lamports"] or 0)
    if acquired <= 0:
        raise CanaryLedgerError("position has no recorded acquired amount to sell")
    ttl = int(approval_ttl_seconds() if ttl_seconds is None else ttl_seconds)
    approval_id = uuid.uuid4().hex
    try:
        db.execute(
            """INSERT INTO live_canary_approvals(
                 approval_id,kind,asset_key,mint,target_lamports,min_out_lamports,
                 slippage_bps,position_approval_id,evidence_json,status,
                 created_epoch,expires_epoch,approved_epoch,approved_by_user_id,
                 approved_in_chat_id,updated_epoch
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                approval_id, KIND_EXIT, str(pos["asset_key"]), str(pos["mint"]),
                acquired, 0, int(pos["slippage_bps"] or 0), str(position_approval_id),
                json.dumps({"source": "grokexit"}, sort_keys=True), STATUS_APPROVED,
                now, now + ttl, now, str(user_id), str(chat_id), now,
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        raise CanaryLedgerError("a non-terminal EXIT approval already exists for this position")
    _audit(db, "CANARY_EXIT_APPROVED", approval_id, {"asset_key": pos["asset_key"], "position_approval_id": str(position_approval_id), "approved_by_user_id": str(user_id)})
    return approval_id


def claim_next_approved(db: sqlite3.Connection, *, now: float | None = None) -> dict[str, Any] | None:
    """Atomically move one APPROVED ticket to EXECUTING and return it."""
    ensure_schema(db)
    now = int(time.time() if now is None else now)
    db.execute("BEGIN IMMEDIATE")
    cur = db.execute(
        "SELECT approval_id FROM live_canary_approvals WHERE status=? "
        "ORDER BY approved_epoch ASC LIMIT 1",
        (STATUS_APPROVED,),
    )
    row = cur.fetchone()
    if row is None:
        db.rollback()
        return None
    approval_id = str(row[0])
    claimed = db.execute(
        "UPDATE live_canary_approvals SET status=?,claimed_epoch=?,updated_epoch=? "
        "WHERE approval_id=? AND status=?",
        (STATUS_EXECUTING, now, now, approval_id, STATUS_APPROVED),
    )
    if claimed.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    _audit(db, "CANARY_CLAIMED", approval_id, {})
    return _row(db, approval_id)


def _transition(
    db: sqlite3.Connection,
    approval_id: str,
    *,
    expected: str,
    new_status: str,
    detail: str = "",
    tx_signature: str | None = None,
    acquired_lamports: int | None = None,
    now: float | None = None,
) -> bool:
    now = int(time.time() if now is None else now)
    sets = ["status=?", "updated_epoch=?"]
    params: list[Any] = [str(new_status), now]
    if detail:
        sets.append("outcome_detail=?")
        params.append(str(detail)[:1000])
    if tx_signature is not None:
        sets.append("tx_signature=?")
        params.append(str(tx_signature))
    if acquired_lamports is not None:
        sets.append("acquired_lamports=?")
        params.append(int(acquired_lamports))
    params.extend([str(approval_id), str(expected)])
    cur = db.execute(
        f"UPDATE live_canary_approvals SET {','.join(sets)} WHERE approval_id=? AND status=?",
        params,
    )
    db.commit()
    ok = cur.rowcount == 1
    if ok:
        _audit(db, "CANARY_TRANSITION", approval_id, {"from": expected, "to": new_status, "detail": detail})
    return ok


def mark_broadcast_submitted(db: sqlite3.Connection, approval_id: str, *, now: float | None = None) -> bool:
    return _transition(db, approval_id, expected=STATUS_EXECUTING, new_status=STATUS_BROADCAST_SUBMITTED, now=now)


def mark_confirmed(db: sqlite3.Connection, approval_id: str, *, tx_signature: str, acquired_lamports: int = 0, now: float | None = None) -> bool:
    return _transition(
        db, approval_id, expected=STATUS_BROADCAST_SUBMITTED, new_status=STATUS_CONFIRMED,
        tx_signature=tx_signature, acquired_lamports=(acquired_lamports or None), now=now,
    )


def mark_rejected(db: sqlite3.Connection, approval_id: str, *, status: str, detail: str, from_status: str, now: float | None = None) -> bool:
    if status not in TERMINAL:
        raise CanaryLedgerError(f"not a terminal status: {status}")
    return _transition(db, approval_id, expected=from_status, new_status=status, detail=detail, now=now)


def expire_stale(db: sqlite3.Connection, *, now: float | None = None) -> int:
    ensure_schema(db)
    now = int(time.time() if now is None else now)
    cur = db.execute(
        "UPDATE live_canary_approvals SET status=?,outcome_detail=?,updated_epoch=? "
        "WHERE status=? AND expires_epoch <= ?",
        (STATUS_EXPIRED, "ttl elapsed before approval", now, STATUS_PENDING, now),
    )
    db.commit()
    return cur.rowcount


def cancel_unclaimed(db: sqlite3.Connection, *, reason: str, now: float | None = None) -> int:
    """Cancel PENDING and APPROVED tickets. Never touches EXECUTING+ (may be broadcasting)."""
    ensure_schema(db)
    now = int(time.time() if now is None else now)
    cur = db.execute(
        "UPDATE live_canary_approvals SET status=?,outcome_detail=?,updated_epoch=? "
        "WHERE status IN (?,?)",
        (STATUS_CANCELLED, str(reason)[:200], now, STATUS_PENDING, STATUS_APPROVED),
    )
    db.commit()
    return cur.rowcount


def reconcile_on_start(db: sqlite3.Connection, *, now: float | None = None) -> dict[str, int]:
    """Fail closed on every non-terminal ticket at process start.

    PENDING -> EXPIRED. APPROVED / EXECUTING / BROADCAST_SUBMITTED -> a broadcast
    may be in flight or already sent, so force RECONCILIATION_REQUIRED and never
    auto-resume.
    """
    ensure_schema(db)
    now = int(time.time() if now is None else now)
    expired = db.execute(
        "UPDATE live_canary_approvals SET status=?,outcome_detail=?,updated_epoch=? WHERE status=?",
        (STATUS_EXPIRED, "RESTART_INVALIDATED pending ticket", now, STATUS_PENDING),
    ).rowcount
    reconcile = db.execute(
        "UPDATE live_canary_approvals SET status=?,outcome_detail=?,updated_epoch=? "
        "WHERE status IN (?,?,?)",
        (STATUS_RECONCILIATION_REQUIRED, "RESTART_INVALIDATED approved/executing ticket", now,
         STATUS_APPROVED, STATUS_EXECUTING, STATUS_BROADCAST_SUBMITTED),
    ).rowcount
    db.commit()
    _audit(db, "CANARY_RECONCILE_ON_START", "", {"expired": expired, "reconciliation_required": reconcile})
    return {"expired": int(expired), "reconciliation_required": int(reconcile)}


def needs_reconciliation(db: sqlite3.Connection) -> bool:
    ensure_schema(db)
    row = db.execute(
        "SELECT COUNT(*) FROM live_canary_approvals WHERE status IN (?,?)",
        RECONCILE_OUTCOMES,
    ).fetchone()
    return int(row[0] or 0) > 0


def list_pending(db: sqlite3.Connection, *, now: float | None = None) -> list[dict[str, Any]]:
    ensure_schema(db)
    now = int(time.time() if now is None else now)
    cur = db.execute(
        "SELECT * FROM live_canary_approvals WHERE status IN (?,?) ORDER BY created_epoch ASC",
        (STATUS_PENDING, STATUS_APPROVED),
    )
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]
