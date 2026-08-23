from __future__ import annotations

import threading
import time
from contextlib import closing

from . import sibot as _sibot
from . import sibot_alchemy_history_patch as _alchemy

_PREV_REFRESH_WALLET_HISTORY = _sibot.refresh_wallet_history
_PREV_NEXT_HISTORY_WALLET = _sibot._next_history_wallet
_SERIAL_HISTORY_LOCK = threading.Lock()
_TRANSIENT_RETRY_COOLDOWN_SECONDS = 60
_ZERO_RECONSTRUCTION_RETRY_COOLDOWN_SECONDS = 600


def _retryable_alchemy_error(error: str) -> bool:
    text = str(error or "").lower()
    if "alchemyhistoryerror" not in text and "alchemy " not in text:
        return False
    return any(
        marker in text
        for marker in (
            "http 429",
            "rpc 429",
            "compute units per second",
            "rate limit",
            "retries exhausted",
        )
    )


def _priority_retry_candidate(candidates, rows, now_epoch: int) -> str | None:
    by_wallet = {
        str(row["wallet"] or "").lower(): row
        for row in rows
        if str(row["wallet"] or "").strip()
    }
    for raw_wallet in candidates:
        wallet = str(raw_wallet or "").lower()
        if not wallet:
            continue
        row = by_wallet.get(wallet)
        if row is None:
            continue
        error = str(row["error"] or "")
        fetched_at = _sibot._int(row["fetched_at"], 0)
        if "ETHERSCAN_API_KEY" in error:
            return wallet
        if _retryable_alchemy_error(error) and fetched_at <= now_epoch - _TRANSIENT_RETRY_COOLDOWN_SECONDS:
            return wallet
    return None


def _ranked_rows(conn, chain_id: int, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT lower(wallet) wallet, MIN(rank) best_rank,
                  MAX(closed_trades) closed_trades, MAX(updated_at) updated_at
           FROM rankings
           WHERE chain_id=? AND COALESCE(wallet,'')<>''
           GROUP BY lower(wallet)
           ORDER BY best_rank ASC, closed_trades DESC, updated_at DESC
           LIMIT ?""",
        (int(chain_id), max(1, int(limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def _priority_ranked_candidate(ranked_rows, status_rows, trade_wallets, now_epoch: int) -> str | None:
    """Prioritise currently ranked wallets before old migration backlog.

    Ranked wallets with transient provider errors retry after the existing short
    cooldown. A ranked wallet that previously claimed closed trades but now has
    non-empty Alchemy transfer evidence, zero reconstructed trades and incomplete
    history receives one bounded retry window as well. This targets stale ranking
    inconsistencies without turning successful empty histories into a tight loop.
    """
    status_by_wallet = {
        str(row["wallet"] or "").lower(): row
        for row in status_rows
        if str(row["wallet"] or "").strip()
    }
    with_trades = {str(wallet or "").lower() for wallet in trade_wallets if str(wallet or "").strip()}
    for ranked in ranked_rows:
        wallet = str(ranked.get("wallet") or "").lower()
        if not wallet:
            continue
        status = status_by_wallet.get(wallet)
        if status is None:
            return wallet
        error = str(status["error"] or "")
        fetched_at = _sibot._int(status["fetched_at"], 0)
        if "ETHERSCAN_API_KEY" in error:
            return wallet
        if _retryable_alchemy_error(error) and fetched_at <= now_epoch - _TRANSIENT_RETRY_COOLDOWN_SECONDS:
            return wallet
        if error or wallet in with_trades:
            continue
        if _sibot._int(status["history_complete"], 0):
            continue
        if fetched_at > now_epoch - _ZERO_RECONSTRUCTION_RETRY_COOLDOWN_SECONDS:
            continue
        evidence_rows = _sibot._int(status["normal_rows"], 0) + _sibot._int(status["token_rows"], 0)
        if evidence_rows <= 0:
            continue
        if _sibot._int(ranked.get("closed_trades"), 0) > 0:
            return wallet
    return None


def _next_history_wallet(app, chain):
    """Retry ranked candidate failures before the ordinary age-based backlog."""
    if not _alchemy.alchemy_rpc_url(app, int(chain.chain_id)):
        return _PREV_NEXT_HISTORY_WALLET(app, chain)

    cfg = _sibot.platform_settings(app, chain.chain_id)
    limit = max(20, min(500, _sibot._int(cfg.get("history_candidate_wallets"), 40)))
    now = int(time.time())
    with closing(_sibot.connect(app)) as conn:
        ranked = _ranked_rows(conn, chain.chain_id, min(100, limit))
        status_rows = conn.execute(
            """SELECT wallet,fetched_at,error,history_complete,normal_rows,token_rows
               FROM wallet_history_status WHERE chain_id=?""",
            (chain.chain_id,),
        ).fetchall()
        trade_wallets = [
            row["wallet"]
            for row in conn.execute(
                "SELECT DISTINCT lower(wallet) wallet FROM wallet_trades WHERE chain_id=?",
                (chain.chain_id,),
            ).fetchall()
        ]

    chosen = _priority_ranked_candidate(ranked, status_rows, trade_wallets, now)
    if chosen:
        return chosen

    # Preserve the previous current-candidate retry behaviour for wallets that are
    # not yet in a ranking row, then fall back to the original age-based queue.
    candidates = [
        str(wallet or "").lower()
        for wallet in _sibot._candidate_wallets(app, chain, limit)
        if str(wallet or "").strip()
    ]
    if candidates:
        error_rows = [row for row in status_rows if str(row["error"] or "")]
        chosen = _priority_retry_candidate(candidates, error_rows, now)
        if chosen:
            return chosen

    return _PREV_NEXT_HISTORY_WALLET(app, chain)


def refresh_wallet_history(app, chain, wallet: str):
    """Serialise Alchemy backfills across EVM chains on this process.

    Alchemy throughput is account-level. Per-chain history workers may otherwise
    start together and consume the same compute-unit bucket concurrently. The
    lock changes only research/backfill scheduling; it does not touch live trade
    execution, signing, risk or market-data WebSockets.
    """
    with _SERIAL_HISTORY_LOCK:
        return _PREV_REFRESH_WALLET_HISTORY(app, chain, wallet)


def install() -> None:
    if getattr(_sibot, "_alchemy_retry_queue_patch_installed", False):
        return
    _sibot._next_history_wallet = _next_history_wallet
    _sibot.refresh_wallet_history = refresh_wallet_history
    _sibot._alchemy_retry_queue_patch_installed = True


install()
