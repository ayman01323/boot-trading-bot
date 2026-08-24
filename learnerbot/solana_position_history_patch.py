from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque
from contextlib import closing
from decimal import Decimal

from . import solana_sibot as _sol

_PREV_CONNECT = _sol.connect
_PREV_CLASSIFY_SWAP = _sol.classify_swap
_PREV_REFRESH_WALLET_HISTORY = _sol.refresh_wallet_history
_SCHEMA_LOCK = threading.RLock()
_SCHEMA_READY = set()
_META_LOCK = threading.RLock()
_MATCH_META: dict[str, dict[str, tuple[str, int]]] = {}
_POSITION_METRICS_VERSION = 2


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
        if "position_metrics_version" not in history_cols:
            conn.execute("ALTER TABLE history_status ADD COLUMN position_metrics_version INTEGER NOT NULL DEFAULT 0")
        # Existing rows pre-date balance-proven inventory-cycle reconstruction.
        # Mark them stale so the normal history worker refreshes selected leaders
        # and bounded candidates using its existing cadence; no RPC cadence or
        # quality threshold is relaxed here.
        conn.execute(
            "UPDATE history_status SET fetched_at=0 WHERE COALESCE(position_metrics_version,0)<?",
            (_POSITION_METRICS_VERSION,),
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sol_trades_position "
            "ON trades(wallet,position_id,position_closed,sell_ts)"
        )
        conn.commit()
        _SCHEMA_READY.add(key)


def connect(app):
    conn = _PREV_CONNECT(app)
    _ensure_position_columns(conn, app)
    return conn


def _dec(v, default="0"):
    return _sol._dec(v, default)


def classify_swap(result: dict, wallet: str) -> dict | None:
    """Add transaction-visible pre/post token balances to a classified swap.

    The base classifier already derives these balances to calculate SELL percent,
    but discards them. Keeping them on the in-memory event lets history prove that
    a reconstructed inventory cycle started at zero and returned to zero. Extra
    fields are ignored by the existing leader-event persistence paths.
    """
    event = _PREV_CLASSIFY_SWAP(result, wallet)
    if not event:
        return None
    try:
        _deltas, pre, post, _decimals = _sol._token_state(result, wallet)
        mint = str(event.get("mint") or "")
        event = dict(event)
        event["pre_token_balance_raw"] = int(pre.get(mint, 0))
        event["post_token_balance_raw"] = int(post.get(mint, 0))
    except Exception:
        # Absence of balance proof must fail closed for position-level statistics,
        # while preserving the base event for legacy fragment reconstruction.
        event = dict(event)
        event["pre_token_balance_raw"] = None
        event["post_token_balance_raw"] = None
    return event


def _balance(event: dict, key: str):
    value = event.get(key)
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _match_events(wallet: str, events: list[dict]):
    """Preserve FIFO fragments and tag only balance-proven closed positions.

    A position is eligible for position-level quality metrics only when:
      * its observed BUY cycle starts with transaction-visible pre-balance zero;
      * every later swap's pre-balance equals the previous observed post-balance;
      * reconstructed FIFO quantity never conflicts with the observed balance; and
      * the closing SELL proves post-balance zero.

    Thus partial sells, pre-lookback inventory, token transfers between swaps, or
    missing balance evidence cannot be promoted into a fabricated closed win/loss.
    Fragment trade IDs and P&L maths remain byte-for-byte compatible with the base
    matcher so existing research/audit rows retain their identity.
    """
    lots = defaultdict(deque)
    trades = []
    position_rows = defaultdict(list)
    active: dict[str, dict] = {}
    position_seq = defaultdict(int)
    seq = 0

    for ev in sorted(events, key=lambda x: (x["event_ts"], x["signature"])):
        mint = str(ev["mint"])
        pre_balance = _balance(ev, "pre_token_balance_raw")
        post_balance = _balance(ev, "post_token_balance_raw")

        if ev["action"] == "BUY":
            info = active.get(mint)
            if not lots[mint]:
                position_seq[mint] += 1
                pid = hashlib.sha256(
                    f"solana-position-v2|{wallet}|{mint}|{ev['signature']}|{position_seq[mint]}".encode()
                ).hexdigest()[:32]
                info = {
                    "position_id": pid,
                    "exact": bool(pre_balance == 0 and post_balance is not None),
                    "visible_balance": post_balance,
                }
                active[mint] = info
            else:
                if info is None:
                    # Defensive: existing lots without an active proof state are
                    # never eligible for exact position metrics.
                    pid = str(lots[mint][0].get("position_id") or "")
                    info = {"position_id": pid, "exact": False, "visible_balance": post_balance}
                    active[mint] = info
                if info.get("exact") and (
                    pre_balance is None
                    or info.get("visible_balance") is None
                    or pre_balance != int(info["visible_balance"])
                ):
                    info["exact"] = False
                info["visible_balance"] = post_balance

            pid = str(info.get("position_id") or "")
            lots[mint].append({
                **ev,
                "remaining": int(ev["token_amount_raw"]),
                "remaining_cost": _dec(ev["sol_amount"]),
                "position_id": pid,
            })
            continue

        # SELL
        info = active.get(mint)
        if info is not None and info.get("exact") and (
            pre_balance is None
            or info.get("visible_balance") is None
            or pre_balance != int(info["visible_balance"])
        ):
            info["exact"] = False

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
            pid = str(lot.get("position_id") or "")
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
            if pid:
                position_rows[pid].append(row)
            lot["remaining"] -= qty
            lot["remaining_cost"] = _dec(lot["remaining_cost"]) - cost
            remaining -= qty
            if lot["remaining"] <= 0:
                lots[mint].popleft()

        if info is not None:
            info["visible_balance"] = post_balance
            # A SELL larger than reconstructed inventory proves unknown carried-in
            # inventory was involved; do not call the cycle exact.
            if remaining > 0:
                info["exact"] = False

            if not lots[mint]:
                pid = str(info.get("position_id") or "")
                exact_closed = bool(info.get("exact") and post_balance == 0 and remaining == 0)
                if exact_closed and pid:
                    for row in position_rows.get(pid, []):
                        row["position_closed"] = 1
                active.pop(mint, None)
            elif post_balance == 0:
                # Reconstructed inventory remains but chain-visible balance is zero:
                # evidence is inconsistent, therefore this cycle is never exact.
                info["exact"] = False

    with _META_LOCK:
        _MATCH_META[str(wallet)] = {
            str(r["trade_id"]): (str(r.get("position_id") or ""), int(r.get("position_closed") or 0))
            for r in trades
        }
    return trades


def refresh_wallet_history(app, wallet: str) -> dict:
    # The original routine remains authoritative for RPC fetching, transaction
    # classification, error handling and fragment persistence. Patched classifier
    # and matcher add proof metadata, written only after the original refresh
    # succeeds. No additional RPC request is introduced.
    result = _PREV_REFRESH_WALLET_HISTORY(app, wallet)
    with _META_LOCK:
        metadata = _MATCH_META.pop(str(wallet), None)
    if metadata is None or result.get("error"):
        return result

    with _sol._DB_LOCK, closing(connect(app)) as conn:
        for trade_id, (position_id, position_closed) in metadata.items():
            conn.execute(
                "UPDATE trades SET position_id=?,position_closed=? WHERE wallet=? AND trade_id=?",
                (position_id or None, int(position_closed), str(wallet), trade_id),
            )
        row = conn.execute(
            """SELECT COUNT(DISTINCT position_id) n FROM trades
               WHERE wallet=? AND position_closed=1 AND position_id IS NOT NULL""",
            (str(wallet),),
        ).fetchone()
        closed_positions = int(row["n"] or 0) if row else 0
        conn.execute(
            "UPDATE history_status SET closed_trades=?,position_metrics_version=? WHERE wallet=?",
            (closed_positions, _POSITION_METRICS_VERSION, str(wallet)),
        )
        conn.commit()
    result = dict(result)
    result["closed_trades"] = closed_positions
    result["position_metrics_version"] = _POSITION_METRICS_VERSION
    return result


def install() -> None:
    if getattr(_sol, "_position_history_patch_installed", False):
        return
    _sol.connect = connect
    _sol.classify_swap = classify_swap
    _sol._match_events = _match_events
    _sol.refresh_wallet_history = refresh_wallet_history
    _sol._position_history_patch_installed = True


install()
