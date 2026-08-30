"""Automatic Grok LIVE-canary BUY/SELL preparation, never auto-broadcast.

This wrapper preserves the existing manual-confirmation execution boundary:
- BUY: ``live_canary_runner`` already converts each fresh ``LIVE_READY`` event
  into a short-lived PENDING entry approval.
- SELL: this module watches one confirmed Grok canary position and emits a
  ``CANARY_PENDING`` approval prompt when an existing full-exit risk condition
  is met. The prompt tells the owner to use ``/grokexit <position_id> CONFIRM``.
- No function in this module signs, simulates or broadcasts a transaction.

The wrapper is intentionally reversible: the systemd unit can be changed back
to ``grok_known_assets_bot.live_canary_runner`` without any schema migration.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from typing import Any

from . import live_canary as lc
from . import live_canary_runner as base

AUTO_EXIT_REPEAT_SECONDS = 300.0


def _open_positions(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return confirmed entries that do not yet have a confirmed exit."""
    lc.ensure_schema(db)
    cur = db.execute(
        """
        SELECT e.* FROM live_canary_approvals e
        WHERE e.kind='ENTRY' AND e.status='CONFIRMED'
          AND NOT EXISTS (
            SELECT 1 FROM live_canary_approvals x
            WHERE x.kind='EXIT' AND x.status='CONFIRMED'
              AND x.position_approval_id=e.approval_id
          )
        ORDER BY e.updated_epoch ASC
        """
    )
    names = [c[0] for c in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _nonterminal_exit_exists(db: sqlite3.Connection, position_id: str) -> bool:
    row = db.execute(
        """
        SELECT 1 FROM live_canary_approvals
        WHERE kind='EXIT' AND position_approval_id=?
          AND status IN ('PENDING_APPROVAL','APPROVED','EXECUTING','BROADCAST_SUBMITTED')
        LIMIT 1
        """,
        (str(position_id),),
    ).fetchone()
    return row is not None


def _entry_price_usd(position: dict[str, Any]) -> float:
    spend_usdc = int(position.get("input_micro_usdc") or 0) / 1_000_000.0
    acquired_sol = int(position.get("acquired_lamports") or 0) / 1_000_000_000.0
    if spend_usdc <= 0.0 or acquired_sol <= 0.0:
        return 0.0
    return spend_usdc / acquired_sol


def _net_return_pct(position: dict[str, Any], snap: Any) -> float:
    """Approximate current net return from actual entry spend and fresh exit route.

    The actual entry spend/acquired SOL already embeds entry execution quality,
    so only the fresh exit route cost is subtracted here.
    """
    entry = _entry_price_usd(position)
    reverse_bid = float(getattr(snap, "reverse_bid", 0.0) or 0.0)
    if entry <= 0.0 or reverse_bid <= 0.0:
        return -999.0
    gross = (reverse_bid / entry - 1.0) * 100.0
    exit_cost_bps = (
        max(0.0, float(getattr(snap, "fee_bps", 0.0) or 0.0))
        + max(0.0, float(getattr(snap, "price_impact_bps", 0.0) or 0.0))
        + max(0.0, float(getattr(snap, "slippage_bps", 0.0) or 0.0))
    )
    return gross - exit_cost_bps / 100.0


def _exit_reason(position: dict[str, Any], snap: Any, risk: Any, *, now: float) -> tuple[str | None, float]:
    """Evaluate only approval-prompt conditions; never execute an exit."""
    net = _net_return_pct(position, snap)
    sellable = bool(getattr(snap, "sellable", False)) and float(getattr(snap, "reverse_bid", 0.0) or 0.0) > 0.0
    if not sellable:
        return "NO_SELL_PATH", net

    # Existing canary records do not persist the dynamic PAPER stop. Use the
    # configured minimum stop as a conservative prompt threshold only. The
    # actual sell still requires explicit approval and fresh revalidation.
    stop_pct = float(getattr(risk, "stop_min_pct", 2.5) or 2.5)
    if net <= -stop_pct:
        return "HARD_STOP", net

    liquidity = float(getattr(snap, "liquidity_usd", 0.0) or 0.0)
    if liquidity < float(getattr(risk, "min_liquidity_usd", 0.0) or 0.0) * 0.70:
        return "LIQUIDITY_DETERIORATION", net

    spread = float(getattr(snap, "spread_bps", 0.0) or 0.0)
    if spread > float(getattr(risk, "max_spread_bps", 0.0) or 0.0) * 1.50:
        return "SPREAD_DETERIORATION", net

    opened = float(position.get("updated_epoch") or position.get("created_epoch") or now)
    if max(0.0, now - opened) / 60.0 >= float(getattr(risk, "max_hold_minutes", 60.0) or 60.0):
        return "TIME_STOP", net

    ret_1m = float(getattr(snap, "ret_1m_pct", 0.0) or 0.0)
    if ret_1m <= -0.70 and net > -stop_pct:
        return "MOMENTUM_REVERSAL", net

    tp2 = float(getattr(risk, "take_profit_2_pct", 4.0) or 4.0)
    if net >= tp2:
        return "TAKE_PROFIT_2", net

    # The PAPER engine takes 50% at TP1, but the current LIVE-canary ledger is
    # deliberately full-position only. We therefore prompt the owner for review
    # at TP1 rather than silently changing live position accounting.
    tp1 = float(getattr(risk, "take_profit_1_pct", 2.0) or 2.0)
    if net >= tp1:
        return "TAKE_PROFIT_1_REVIEW", net

    return None, net


def _signal_key(position_id: str) -> str:
    return f"canary_auto_exit_signal:{position_id}"


def prepare_auto_exit_signals(
    journal: Any,
    db: sqlite3.Connection,
    feed: Any,
    assets: dict[str, Any],
    *,
    now: float | None = None,
) -> int:
    """Emit owner-approval prompts for live exit conditions. Returns count."""
    now = float(time.time() if now is None else now)
    if lc.needs_reconciliation(db):
        return 0

    emitted = 0
    for position in _open_positions(db):
        position_id = str(position.get("approval_id") or "")
        asset_key = str(position.get("asset_key") or "")
        if not position_id or _nonterminal_exit_exists(db, position_id):
            continue
        asset = assets.get(asset_key)
        if asset is None or not feed.supported(asset):
            continue
        try:
            envelope = feed.collect(asset, now=now)
            snap = envelope.snapshot
        except Exception as exc:
            base._event(
                journal,
                "CANARY_EXIT_MONITOR_REJECT",
                asset_key,
                {"position_approval_id": position_id, "reason": f"fresh feed failed: {type(exc).__name__}: {exc}"},
            )
            continue

        reason, net = _exit_reason(position, snap, feed.risk, now=now)
        if not reason:
            continue

        previous = journal.get_state(_signal_key(position_id), {})
        if isinstance(previous, dict):
            same_reason = str(previous.get("reason") or "") == reason
            last_ts = float(previous.get("ts") or 0.0)
            if same_reason and now - last_ts < AUTO_EXIT_REPEAT_SECONDS:
                continue

        target = int(position.get("acquired_lamports") or 0)
        approve_with = f"/grokexit {position_id} CONFIRM"
        payload = {
            "kind": "EXIT_SIGNAL",
            "position_approval_id": position_id,
            "target_lamports": target,
            "input_micro_usdc": 0,
            "min_out_lamports": 0,
            "reason": reason,
            "estimated_net_pct": round(float(net), 4),
            "approve_with": approve_with,
            "automatic_detection": True,
            "automatic_broadcast": False,
        }
        base._event(journal, "CANARY_PENDING", asset_key, payload)
        journal.set_state(_signal_key(position_id), {"reason": reason, "ts": now})
        emitted += 1
    return emitted


_ORIGINAL_RUN_ONCE = base.run_once


def _run_once_with_auto_exit(journal, db, feed, assets, cursor, *, now=None):
    cursor = _ORIGINAL_RUN_ONCE(journal, db, feed, assets, cursor, now=now)
    prepare_auto_exit_signals(journal, db, feed, assets, now=now)
    return cursor


def main(argv: list[str] | None = None) -> int:
    # Patch only this process. Reverting the systemd ExecStart restores the
    # original runner immediately; there is no persistent schema migration.
    base.run_once = _run_once_with_auto_exit
    return base.main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
