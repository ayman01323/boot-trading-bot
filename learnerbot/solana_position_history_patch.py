from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque
from contextlib import closing
from decimal import Decimal

from . import solana_sibot as _sol

_PREV_CONNECT = _sol.connect
_PREV_REFRESH_WALLET_HISTORY = _sol.refresh_wallet_history
_SCHEMA_LOCK = threading.RLock()
_SCHEMA_READY = set()
_META_LOCK = threading.RLock()
_MATCH_META: dict[str, dict[str, tuple[str, int]]] = {}


def _ensure_position_columns(conn, app) -> None:
    key = str(_sol.db_path(app))
    if key in _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if key in _SCHEMA_READY:
            return
        trade_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "position_id" not in trade_cols:
            conn.execute("ALTER TABLE trades ADD COLUMN position_id TEXT")
        if "position_closed" not in trade_cols:
            conn.execute("ALTER TABLE trades ADD COLUMN position_closed INTEGER NOT NULL DEFAULT 0")
        history_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(history_status)").fetchall()}
        added_version = "position_metrics_version" not in history_cols
        if added_version:
            conn.execute("ALTER TABLE history_status ADD COLUMN position_metrics_version INTEGER NOT NULL DEFAULT 0")
        # Existing rows pre-date exact inventory-cycle reconstruction. Mark them
        # immediately stale so the normal history worker refreshes selected leaders
        # and candidates (leaders are already ordered first) without changing its
        # RPC cadence or bypassing any quality gate.
        conn.execute("UPDATE history_status SET fetched_at=0 WHERE COALESCE(position_metrics_version,0)<1")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sol_trades_position ON trades(wallet,position_id,position_closed,sell_ts)")
        conn.commit()
        _SCHEMA_READY.add(key)


def connect(app):
    conn = _PREV_CONNECT(app)
    _ensure_position_columns(conn, app)
    return conn


def _dec(v, default="0"):
    return _sol._dec(v, default)


def _match_events(wallet: str, events: list[dict]):
    """FIFO-match swaps while tagging exact inventory-cycle closure.

    This deliberately preserves the original trade-fragment IDs and P&L maths.
    The only added information is a stable position_id and a position_closed bit.
    A cycle closes only when the reconstructed FIFO inventory for that mint reaches
    zero; partially sold inventory therefore cannot be counted as a closed win.
    """
    lots = defaultdict(deque)
    trades = []
    position_rows = defaultdict(list)
    active_position: dict[str, str] = {}
    position_seq = defaultdict(int)
    seq = 0

    for ev in sorted(events, key=lambda x: (x["event_ts"], x["signature"])):
        mint = ev["mint"]
        if ev["action"] == "BUY":
            if not lots[mint]:
                position_seq[mint] += 1
                pid = hashlib.sha256(
                    f"solana-position|{wallet}|{mint}|{ev['signature']}|{position_seq[mint]}".encode()
                ).hexdigest()[:32]
                active_position[mint] = pid
            pid = active_position[mint]
            lots[mint].append({
                **ev,
                "remaining": int(ev["token_amount_raw"]),
                "remaining_cost": _dec(ev["sol_amount"]),
                "position_id": pid,
            })
            continue

        remaining = int(ev["token_amount_raw"])
        original = max(1, remaining)
        while remaining > 0 and lots[mint]:
            lot = lots[mint][0]
            qty = min(remaining, int(lot["remaining"]))
            buy_fraction = Decimal(qty) / Decimal(max(1, int(lot["remaining"])))
            sell_fraction = Decimal(qty) / Decimal(original)
            cost = _dec(lot["remaining_cost"]) * buy_fraction
            proceeds = _dec(ev["sol_amount"]) * sell_fraction
            net = proceeds - cost
            seq += 1
            tid = hashlib.sha256(
                f"solana|{wallet}|{lot['signature']}|{ev['signature']}|{mint}|{seq}".encode()
            ).hexdigest()[:32]
            pid = str(lot["position_id"])
            row = {
                "trade_id": tid,
                "wallet": wallet,
                "mint": mint,
                "decimals": int(ev.get("decimals") or lot.get("decimals") or 0),
                "buy_signature": lot["signature"],
                "sell_signature": ev["signature"],
                "buy_ts": int(lot["event_ts"]),
                "sell_ts": int(ev["event_ts"]),
                "token_amount_raw": str(qty),
                "cost_sol": str(cost),
                "proceeds_sol": str(proceeds),
                "net_sol": str(net),
                "hold_seconds": max(0, int(ev["event_ts"]) - int(lot["event_ts"])),
                "source": "SOLANA_FINALIZED_SOL_DELTA_FIFO",
                "updated_at": int(time.time()),
                "position_id": pid,
                "position_closed": 0,
            }
            trades.append(row)
            position_rows[pid].append(row)
            lot["remaining"] -= qty
            lot["remaining_cost"] = _dec(lot["remaining_cost"]) - cost
            remaining -= qty
            if lot["remaining"] <= 0:
                lots[mint].popleft()

        if not lots[mint]:
            pid = active_position.pop(mint, None)
            if pid:
                for row in position_rows.get(pid, []):
                    row["position_closed"] = 1

    with _META_LOCK:
        _MATCH_META[str(wallet)] = {
            str(r["trade_id"]): (str(r["position_id"]), int(r["position_closed"])) for r in trades
        }
    return trades


def refresh_wallet_history(app, wallet: str) -> dict:
    # The original routine remains authoritative for RPC fetching, transaction
    # classification, error handling and fragment persistence. Our patched matcher
    # supplies closure metadata, which is written only after that routine succeeds.
    result = _PREV_REFRESH_WALLET_HISTORY(app, wallet)
    with _META_LOCK:
        metadata = _MATCH_META.pop(str(wallet), None)
    if metadata is None or result.get("error"):
        return result

    with _sol._DB_LOCK, closing(connect(app)) as conn:
        for trade_id, (position_id, position_closed) in metadata.items():
            conn.execute(
                "UPDATE trades SET position_id=?,position_closed=? WHERE wallet=? AND trade_id=?",
                (position_id, int(position_closed), str(wallet), trade_id),
            )
        row = conn.execute(
            """SELECT COUNT(DISTINCT position_id) n FROM trades
               WHERE wallet=? AND position_closed=1 AND position_id IS NOT NULL""",
            (str(wallet),),
        ).fetchone()
        closed_positions = int(row["n"] or 0) if row else 0
        conn.execute(
            "UPDATE history_status SET closed_trades=?,position_metrics_version=1 WHERE wallet=?",
            (closed_positions, str(wallet)),
        )
        conn.commit()
    result = dict(result)
    result["closed_trades"] = closed_positions
    result["position_metrics_version"] = 1
    return result


def install() -> None:
    if getattr(_sol, "_position_history_patch_installed", False):
        return
    _sol.connect = connect
    _sol._match_events = _match_events
    _sol.refresh_wallet_history = refresh_wallet_history
    _sol._position_history_patch_installed = True


install()
