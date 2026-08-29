"""Grok LIVE-canary orchestrator.

Consumes ``LIVE_READY`` tickets produced by the PAPER runner's LIVE_READINESS
pipeline, turns each into a single-use PENDING approval, and — only after an
explicit ``/grokapprove <id> CONFIRM`` — revalidates and broadcasts exactly one
guarded swap through the audited learnerbot executor.

Default OFF. Requires ALL of: ``--enable-live-canary`` flag, and the control
file with ``armed`` + ``live_readiness_enabled`` + ``live_canary_enabled`` true.
"""
from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import time
from typing import Any

from . import live_canary as lc
from .control import control_path, is_live_canary_enabled, load_state, save_state
from .core import Journal, load_config
from .live_feed import SolanaNativeLiveFeed, SOL_MINT
from .live_readiness import assess_live_readiness

POLL_SECONDS = 3.0


def _event(journal: Journal, kind: str, asset_key: str | None, payload: dict[str, Any]) -> None:
    journal.event(kind, asset_key, {**payload, "mode": "LIVE_CANARY", "component": "grok-live-canary"})
    print(json.dumps({"kind": kind, "asset_key": asset_key or "", **payload}, sort_keys=True), flush=True)


def _disable_canary(reason: str) -> None:
    state = load_state()
    save_state(
        armed=bool(state.get("armed")),
        live_readiness_enabled=bool(state.get("live_readiness_enabled")),
        live_canary_enabled=False,
        updated_by=f"canary-runner:{reason}",
    )


def _latest_event_id(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()
    return int(row[0] if row else 0)


def _ingest_live_ready(journal: Journal, db: sqlite3.Connection, cursor: int, *, now: float) -> int:
    rows = db.execute(
        "SELECT id, asset_key, payload FROM events WHERE id>? AND kind='LIVE_READY' ORDER BY id ASC LIMIT 50",
        (int(cursor),),
    ).fetchall()
    for event_id, asset_key, payload in rows:
        cursor = int(event_id)
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("ready"):
            continue
        if int(now) > int(data.get("expires_epoch") or 0):
            _event(journal, "CANARY_SKIP", asset_key, {"reason": "LIVE_READY_TICKET_STALE", "event_id": event_id})
            continue
        micro_usdc = int(data.get("entry_input_micro_usdc") or 0)
        min_out = int(data.get("entry_min_out_lamports") or 0)
        if micro_usdc <= 0 or min_out <= 0:
            _event(journal, "CANARY_SKIP", asset_key, {"reason": "LIVE_READY_MISSING_INVARIANTS", "event_id": event_id})
            continue
        try:
            approval_id = lc.create_pending_entry(
                db,
                asset_key=str(asset_key or ""),
                mint=SOL_MINT,
                input_micro_usdc=micro_usdc,
                target_lamports=lc.TARGET_LAMPORTS,
                min_out_lamports=min_out,
                slippage_bps=int(data.get("slippage_bps") or 0),
                evidence={
                    "source_event_id": int(event_id),
                    "route_id": str(data.get("route_id") or ""),
                    "roundtrip_loss_pct": data.get("roundtrip_loss_pct"),
                    "entry_impact_bps": data.get("entry_impact_bps"),
                    "reverse_impact_bps": data.get("reverse_impact_bps"),
                    "stress_impact_bps": data.get("stress_impact_bps"),
                    "quoted_sol_out": data.get("quoted_sol_out"),
                },
                now=now,
            )
            _event(journal, "CANARY_PENDING", asset_key, {
                "approval_id": approval_id,
                "target_lamports": lc.TARGET_LAMPORTS,
                "input_micro_usdc": micro_usdc,
                "min_out_lamports": min_out,
                "approve_with": f"/grokapprove {approval_id} CONFIRM",
            })
        except lc.CanaryLedgerError as exc:
            _event(journal, "CANARY_SKIP", asset_key, {"reason": str(exc), "event_id": event_id})
    return cursor


def _revalidate_entry(feed: SolanaNativeLiveFeed, assets: dict, ticket: dict, *, now: float) -> tuple[bool, str]:
    asset = assets.get(str(ticket.get("asset_key") or ""))
    if asset is None or not feed.supported(asset):
        return False, "asset no longer supported"
    try:
        envelope = feed.collect(asset, now=now)
    except Exception as exc:
        return False, f"fresh feed failed: {type(exc).__name__}: {exc}"
    result = assess_live_readiness(envelope.snapshot, feed.settings, now=now)
    if not result.ready:
        return False, f"revalidation not ready: {result.reason}"
    if int(result.entry_min_out_lamports) < int(ticket.get("min_out_lamports") or 0):
        return False, (
            f"route degraded: fresh min_out {result.entry_min_out_lamports} "
            f"< approved {ticket.get('min_out_lamports')}"
        )
    return True, "ok"


def _execute_ticket(journal: Journal, db: sqlite3.Connection, ticket: dict, feed, assets, *, now: float) -> None:
    from . import live_execution as lx

    approval_id = str(ticket["approval_id"])
    kind = str(ticket["kind"])
    asset_key = str(ticket.get("asset_key") or "")

    if not is_live_canary_enabled():
        lc.mark_rejected(db, approval_id, status=lc.STATUS_REJECTED_REVALIDATION,
                         detail="canary disabled before execution", from_status=lc.STATUS_EXECUTING)
        _event(journal, "CANARY_REJECTED", asset_key, {"approval_id": approval_id, "reason": "canary disabled"})
        return

    if kind == lc.KIND_ENTRY:
        ok, reason = _revalidate_entry(feed, assets, ticket, now=now)
        if not ok:
            lc.mark_rejected(db, approval_id, status=lc.STATUS_REJECTED_REVALIDATION,
                             detail=reason, from_status=lc.STATUS_EXECUTING)
            _event(journal, "CANARY_REJECTED", asset_key, {"approval_id": approval_id, "reason": reason})
            return
        ok, reason = lx.preflight_funding(need_input_micro_usdc=int(ticket["input_micro_usdc"]))
        if not ok:
            lc.mark_rejected(db, approval_id, status=lc.STATUS_REJECTED_REVALIDATION,
                             detail=reason, from_status=lc.STATUS_EXECUTING)
            _event(journal, "CANARY_REJECTED", asset_key, {"approval_id": approval_id, "reason": reason})
            return
        in_mint, out_mint = lx.USDC_MINT, lx.WSOL_MINT
        amount_raw = int(ticket["input_micro_usdc"])
        min_out = int(ticket["min_out_lamports"])
    else:
        amount_raw = int(ticket["target_lamports"])
        ok, reason = lx.preflight_exit_balance(need_sol_lamports=amount_raw)
        if not ok:
            lc.mark_rejected(db, approval_id, status=lc.STATUS_REJECTED_REVALIDATION,
                             detail=reason, from_status=lc.STATUS_EXECUTING)
            _event(journal, "CANARY_REJECTED", asset_key, {
                "approval_id": approval_id,
                "reason": reason,
                "stage": "EXIT_ONCHAIN_BALANCE",
            })
            return
        in_mint, out_mint = lx.WSOL_MINT, lx.USDC_MINT
        min_out = 0

    def _submitted() -> None:
        lc.mark_broadcast_submitted(db, approval_id)
        _event(journal, "CANARY_BROADCAST_SUBMITTED", asset_key, {"approval_id": approval_id, "kind": kind})

    try:
        out = lx.execute_swap(
            input_mint=in_mint, output_mint=out_mint, amount_raw=amount_raw,
            min_out_raw=min_out, on_broadcast_submitted=_submitted,
        )
    except lx.ExecPreBroadcastError as exc:
        lc.mark_rejected(db, approval_id, status=lc.STATUS_SIMULATION_FAILED,
                         detail=str(exc), from_status=lc.STATUS_EXECUTING)
        _event(journal, "CANARY_REJECTED", asset_key, {"approval_id": approval_id, "reason": f"pre-broadcast: {exc}"})
        return
    except lx.ExecPostLandError as exc:
        lc.mark_rejected(db, approval_id, status=lc.STATUS_RECONCILIATION_REQUIRED,
                         detail=f"{exc} sig={exc.signature}", from_status=lc.STATUS_BROADCAST_SUBMITTED)
        _disable_canary("post_land_unproven")
        _event(journal, "CANARY_RECONCILIATION", asset_key, {"approval_id": approval_id, "signature": exc.signature, "reason": str(exc)})
        return
    except (lx.ExecAmbiguousError, lx.ExecConfigError) as exc:
        lc.mark_rejected(db, approval_id, status=lc.STATUS_UNKNOWN_OUTCOME,
                         detail=str(exc), from_status=lc.STATUS_BROADCAST_SUBMITTED)
        _disable_canary("broadcast_ambiguous")
        _event(journal, "CANARY_RECONCILIATION", asset_key, {"approval_id": approval_id, "reason": str(exc)})
        return

    lc.mark_confirmed(db, approval_id, tx_signature=out["signature"],
                      acquired_lamports=int(out["out_raw"]) if kind == lc.KIND_ENTRY else 0)
    _event(journal, "CANARY_EXECUTED", asset_key, {
        "approval_id": approval_id, "kind": kind, "signature": out["signature"],
        "out_raw": out["out_raw"], "wallet_delta_lamports": out.get("wallet_delta_lamports"),
    })


def run_once(journal: Journal, db: sqlite3.Connection, feed, assets, cursor: int, *, now: float | None = None) -> int:
    now = float(time.time() if now is None else now)
    lc.expire_stale(db, now=now)
    if lc.needs_reconciliation(db):
        return cursor
    cursor = _ingest_live_ready(journal, db, cursor, now=now)
    ticket = lc.claim_next_approved(db, now=now)
    if ticket is not None:
        _execute_ticket(journal, db, ticket, feed, assets, now=now)
    return cursor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grok LIVE-canary orchestrator (default OFF)")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--db", default="state.sqlite3")
    parser.add_argument("--enable-live-canary", action="store_true",
                        help="required opt-in; without it the process refuses to start")
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.enable_live_canary:
        raise SystemExit("Refusing to start: --enable-live-canary was not passed")
    assets, risk, raw = load_config(args.config)
    journal = Journal(args.db)
    db = journal.db
    lc.ensure_schema(db)
    recon = lc.reconcile_on_start(db)
    feed = SolanaNativeLiveFeed(assets, risk, journal, raw)
    supported = {a.key: a for a in assets.values() if feed.supported(a)}
    _event(journal, "CANARY_RUNNER_START", None, {
        "reconcile_on_start": recon, "supported_assets": sorted(supported),
        "hard_cap_lamports": lc.HARD_CAP_LAMPORTS, "target_lamports": lc.TARGET_LAMPORTS,
        "control_file": str(control_path()),
    })
    running = True

    def _stop(_s, _f) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    cursor = _latest_event_id(db)
    while running:
        started = time.time()
        try:
            if is_live_canary_enabled():
                cursor = run_once(journal, db, feed, assets, cursor)
            else:
                # Idle: still expire stale pending tickets so nothing lingers.
                lc.expire_stale(db)
        except Exception as exc:  # keep the daemon alive; never assume trade state
            _event(journal, "CANARY_RUNNER_ERROR", None, {"error": f"{type(exc).__name__}: {exc}"})
        if args.once:
            break
        time.sleep(max(0.0, POLL_SECONDS - (time.time() - started)))

    _event(journal, "CANARY_RUNNER_STOP", None, {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
