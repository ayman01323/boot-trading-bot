from __future__ import annotations

"""Redacted aggregate health for the protected SiBot 1 Solana LIVE bridge.

This module is observability-only.  It reads the bridge SQLite database from the
root-owned learnerbot process and exposes aggregate counts through the existing
redacted runtime snapshot.  It never exports Telegram IDs, wallet addresses,
mints, candidate IDs, transaction signatures, RPC URLs or raw provider errors.
"""

import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import sibot1_runtime_diag_export_patch as _diag

_PREV_SNAPSHOT = _diag.snapshot
_MAX_ATTEMPTS = 300


def _failure_class(status: str, error: str) -> str:
    s = str(status or "UNKNOWN").upper()
    e = str(error or "").lower()
    if s == "EXECUTED":
        return "executed"
    if any(x in e for x in ("pool", "rugcheck", "liquid", "reverse", "impact", "honeypot", "freeze", "mint authority")):
        return "live_market_safety"
    if any(x in e for x in ("quote", "jupiter", "route")):
        return "quote_or_route"
    if "simulation" in e:
        return "simulation"
    if any(x in e for x in ("signer", "permission", "fund", "armed", "live is off", "auto is off")):
        return "readiness"
    if s.startswith("BLOCKED_"):
        return "blocked_other"
    if any(x in s for x in ("FAILED", "REJECTED", "INVALID")):
        return "execution_failure"
    if s.startswith("EXIT_"):
        return "exit_state"
    return "other"


def _solana_attempt_health(app) -> dict:
    path = Path(app.data_dir) / "sibot1_solana_live_bridge.sqlite3"
    base = {
        "available": False,
        "redacted": True,
        "identifiers_exported": False,
        "transactions_exported": False,
        "attempt_rows_tail": 0,
        "status_by_engine": {},
        "kind_by_engine": {},
        "failure_class_by_engine": {},
        "latest_by_engine": {},
        "positions_by_engine": {},
    }
    if not path.exists():
        base["reason"] = "bridge_db_not_created"
        return base

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        attempts = conn.execute(
            "SELECT engine_id,kind,status,updated_at,error "
            "FROM attempts ORDER BY updated_at DESC LIMIT ?",
            (_MAX_ATTEMPTS,),
        ).fetchall()
        positions = conn.execute(
            "SELECT engine_id,status,COUNT(*) n FROM positions "
            "GROUP BY engine_id,status ORDER BY engine_id,status"
        ).fetchall()
        conn.close()
    except Exception as exc:
        base["error"] = type(exc).__name__
        return base

    status_by_engine = defaultdict(Counter)
    kind_by_engine = defaultdict(Counter)
    class_by_engine = defaultdict(Counter)
    latest_by_engine = {}
    for row in attempts:
        engine = str(row["engine_id"] or "unknown").lower()[:40]
        kind = str(row["kind"] or "UNKNOWN").upper()[:40]
        status = str(row["status"] or "UNKNOWN").upper()[:80]
        error_class = _failure_class(status, str(row["error"] or ""))
        status_by_engine[engine][status] += 1
        kind_by_engine[engine][kind] += 1
        class_by_engine[engine][error_class] += 1
        if engine not in latest_by_engine:
            updated = int(row["updated_at"] or 0)
            latest_by_engine[engine] = {
                "kind": kind,
                "status": status,
                "updated_at": updated,
                "age_seconds": max(0, int(time.time()) - updated) if updated else None,
                "error_class": error_class,
            }

    positions_by_engine: dict[str, dict[str, int]] = {}
    for row in positions:
        engine = str(row["engine_id"] or "unknown").lower()[:40]
        status = str(row["status"] or "UNKNOWN").upper()[:80]
        positions_by_engine.setdefault(engine, {})[status] = int(row["n"] or 0)

    return {
        "available": True,
        "redacted": True,
        "identifiers_exported": False,
        "transactions_exported": False,
        "updated_epoch": int(time.time()),
        "attempt_rows_tail": len(attempts),
        "status_by_engine": {k: dict(v) for k, v in sorted(status_by_engine.items())},
        "kind_by_engine": {k: dict(v) for k, v in sorted(kind_by_engine.items())},
        "failure_class_by_engine": {k: dict(v) for k, v in sorted(class_by_engine.items())},
        "latest_by_engine": latest_by_engine,
        "positions_by_engine": positions_by_engine,
    }


def snapshot(app) -> dict:
    out = _PREV_SNAPSHOT(app)
    out["schema_version"] = max(9, int(out.get("schema_version") or 0))
    out["solana_live_attempt_health"] = _solana_attempt_health(app)
    return out


def install() -> None:
    if getattr(_diag, "_sibot1_solana_attempt_diag_installed", False):
        return
    _diag.snapshot = snapshot
    _diag._sibot1_solana_attempt_diag_installed = True
    print(
        "[sibot1-solana-attempt-diag] redacted=true attempts=aggregate "
        "identifiers=false transactions=false"
    )


install()
