from __future__ import annotations

"""Learner-only Solana Telegram pool context.

Every position-specific Solana notification routed through ``solana_live_patch``
gets the same liquidity context used by NewPoll45: pool at open, current pool,
change since open, and a DEX Viewer link.  Generic health/menu messages that do
not identify a position are left untouched rather than being assigned an
arbitrary pool.
"""

import re
from contextlib import closing

from . import solana_live_patch as _live
from . import solana_sibot as _sol
from . import telegram_learner_position_update_patch as _position

_PREV_INSERT_LIVE_POSITION = _live._insert_live_position
_PREV_NOTIFY = _live._notify
_POOL_LABEL = "POOL CONTEXT"


def _row(conn, query: str, params: tuple) -> dict:
    result = conn.execute(query, params).fetchone()
    return dict(result) if result else {}


def _resolve_position(app, tid: str, text: str) -> dict:
    message = str(text or "")

    # Most emergency/stuck notices already include Position: <code>...</code>.
    match = re.search(r"Position:\s*<code>([^<]+)</code>", message, flags=re.I)
    if match:
        pid = match.group(1).strip()
        try:
            with closing(_sol.connect(app)) as conn:
                found = _row(
                    conn,
                    "SELECT * FROM positions WHERE telegram_id=? AND position_id=? LIMIT 1",
                    (str(tid), pid),
                )
            if found:
                return found
        except Exception:
            pass

    # BUY confirmations and several owner notices carry Mint/Token explicitly.
    match = re.search(r"(?:Mint|Token):\s*<code>([^<]+)</code>", message, flags=re.I)
    if match:
        mint = match.group(1).strip()
        try:
            with closing(_sol.connect(app)) as conn:
                found = _row(
                    conn,
                    """SELECT * FROM positions WHERE telegram_id=? AND mint=?
                       ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END,
                                COALESCE(closed_at,0) DESC,updated_at DESC LIMIT 1""",
                    (str(tid), mint),
                )
            if found:
                return found
        except Exception:
            pass

    # SELL confirmations do not currently include the mint, but they do include
    # the transaction signature that has already been persisted on the position.
    match = re.search(r"TX:\s*<code>([^<]+)</code>", message, flags=re.I)
    if match:
        signature = match.group(1).strip()
        try:
            with closing(_sol.connect(app)) as conn:
                found = _row(
                    conn,
                    """SELECT * FROM positions WHERE telegram_id=?
                       AND (exit_signature=? OR leader_buy_signature=?)
                       ORDER BY updated_at DESC LIMIT 1""",
                    (str(tid), signature, signature),
                )
            if found:
                return found
        except Exception:
            pass

    # The immediate-leader-exit blocked alert historically omitted both mint and
    # position id. It marks leader_exit_pending before notifying. Use that row only
    # when it is unambiguous for this Telegram account.
    if "leader exit blocked before execution" in message.lower():
        try:
            with closing(_sol.connect(app)) as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        """SELECT * FROM positions WHERE telegram_id=? AND status='OPEN'
                           AND mode='LIVE' AND leader_exit_pending=1 ORDER BY updated_at DESC""",
                        (str(tid),),
                    ).fetchall()
                ]
            if len(rows) == 1:
                return rows[0]
        except Exception:
            pass

    return {}


def insert_live_position_with_pool_open(app, tid, rank, event, trade, allocation, cfg):
    result = _PREV_INSERT_LIVE_POSITION(app, tid, rank, event, trade, allocation, cfg)
    try:
        pid = str(result[0])
        mint = str((event or {}).get("mint") or "")
        _position.capture_pool_open(app, pid, mint)
    except Exception as exc:
        # BUY success/accounting must never be rolled back because market-data
        # presentation was unavailable. The NewPoll report will state that the
        # open baseline could not be captured.
        print("[learner-pool-context] open_capture", type(exc).__name__, flush=True)
    return result


def notify_with_pool_context(app, tid, text):
    message = str(text or "")
    if _POOL_LABEL in message:
        return _PREV_NOTIFY(app, tid, message)

    position = _resolve_position(app, str(tid), message)
    if position:
        try:
            context = _position.pool_context_html(app, position)
            # Replace the internal NewPoll marker with a Telegram-safe visible
            # heading when this block is appended to ordinary notifications.
            context = context.replace(_position._POOL_CONTEXT_MARKER, f"💧 <b>{_POOL_LABEL}</b>")
            message = message.rstrip() + "\n\n" + context
        except Exception as exc:
            print("[learner-pool-context] render", type(exc).__name__, flush=True)
    return _PREV_NOTIFY(app, tid, message)


def install() -> None:
    if getattr(_live, "_learner_pool_context_installed", False):
        return
    _live._insert_live_position = insert_live_position_with_pool_open
    _live._notify = notify_with_pool_context
    _live._learner_pool_context_installed = True
    print(
        "[learner-pool-context] active=true pool_at_open=persisted pool_current=true "
        "pool_change_since_open=true dex_viewer=true generic_messages=unchanged",
        flush=True,
    )


install()

# These presentation overlays must load after the pool wrapper so confirmed BUY,
# SELL and reconciliation messages keep using one complete context pipeline.
from . import telegram_learner_complete_market_context_patch  # noqa: E402,F401
from . import telegram_learner_newpoll_full_format_patch  # noqa: E402,F401
