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


def _legacy_etherscan_error(error: str) -> bool:
    """True for any pre-Alchemy Etherscan-origin failure string.

    Older rows are not limited to the missing-key message. Depending on the key and
    chain they can also say Invalid API Key, NOTOK, or free API access unsupported.
    Once Alchemy is the configured history provider, all of those rows are migration
    backlog and should be refreshed through Alchemy rather than waiting the normal
    history_refresh_hours interval.
    """
    return "etherscan" in str(error or "").lower()


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
        if _legacy_etherscan_error(error):
            return wallet
        if _retryable_alchemy_error(error) and fetched_at <= now_epoch - _TRANSIENT_RETRY_COOLDOWN_SECONDS:
            return wallet
    return None


def _next_history_wallet(app, chain):
    """Retry priority candidate throttles without waiting the normal 12h age.

    Any legacy Etherscan-origin row remains immediately migratable when an Alchemy
    endpoint is configured. Alchemy 429 rows use a short cooldown so bounded backoff
    gets another try without creating a tight provider-throttle loop.
    """
    if not _alchemy.alchemy_rpc_url(app, int(chain.chain_id)):
        return _PREV_NEXT_HISTORY_WALLET(app, chain)

    cfg = _sibot.platform_settings(app, chain.chain_id)
    limit = max(20, min(500, _sibot._int(cfg.get("history_candidate_wallets"), 40)))
    candidates = [
        str(wallet or "").lower()
        for wallet in _sibot._candidate_wallets(app, chain, limit)
        if str(wallet or "").strip()
    ]
    if candidates:
        with closing(_sibot.connect(app)) as conn:
            rows = conn.execute(
                """SELECT wallet, fetched_at, error
                   FROM wallet_history_status
                   WHERE chain_id=? AND error<>''""",
                (chain.chain_id,),
            ).fetchall()
        chosen = _priority_retry_candidate(candidates, rows, int(time.time()))
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
