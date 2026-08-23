from __future__ import annotations

import threading
import time
from contextlib import closing

from . import sibot as _sibot
from . import sibot_alchemy_history_patch as _alchemy
from . import sibot_alchemy_internal_trace_patch as _internal
from . import sibot_alchemy_retry_queue_patch as _retry

# BNB Smart Chain and Arbitrum require debug traces for internal native proceeds.
# Process them progressively so one high-activity wallet cannot monopolise the
# single EVM history worker and leave every other chain stale for hours.
_TRACE_REQUIRED_CHAINS = {56, 42161}
_TRACE_BATCH_SIZE = 4
_PROGRESS_RETRY_SECONDS = 8

_PREV_REFRESH_WALLET_HISTORY = _sibot.refresh_wallet_history
_PREV_NEXT_HISTORY_WALLET = _sibot._next_history_wallet
_CACHE_LOCK = threading.Lock()
_TRACE_CACHE: dict[tuple[int, str], dict[str, list[dict]]] = {}


def _progress_candidate(candidates, rows, now_epoch: int) -> str | None:
    by_wallet = {
        str(row["wallet"] or "").lower(): int(row["fetched_at"] or 0)
        for row in rows
        if str(row["wallet"] or "").strip()
    }
    for raw in candidates:
        wallet = str(raw or "").lower()
        if wallet and wallet in by_wallet and by_wallet[wallet] <= now_epoch - _PROGRESS_RETRY_SECONDS:
            return wallet
    return None


def _next_history_wallet(app, chain):
    if int(chain.chain_id) not in _TRACE_REQUIRED_CHAINS:
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
                """SELECT wallet,fetched_at FROM wallet_history_status
                   WHERE chain_id=? AND error LIKE 'AlchemyHistoryProgress:%'""",
                (int(chain.chain_id),),
            ).fetchall()
        chosen = _progress_candidate(candidates, rows, int(time.time()))
        if chosen:
            return chosen
    return _PREV_NEXT_HISTORY_WALLET(app, chain)


def _trace_chunk(url: str, wallet: str, tx_hashes: list[str], ts_by_hash: dict[str, int]) -> dict[str, list[dict]]:
    if not tx_hashes:
        return {}
    payload = [
        {
            "jsonrpc": "2.0",
            "id": idx + 1,
            "method": "debug_traceTransaction",
            "params": [
                tx_hash,
                {
                    "tracer": "callTracer",
                    "tracerConfig": {"onlyTopCall": False},
                    "timeout": "12s",
                },
            ],
        }
        for idx, tx_hash in enumerate(tx_hashes)
    ]
    data = _alchemy._post_json(url, payload, 30, "debug_traceTransaction")
    if not isinstance(data, list):
        raise RuntimeError("Alchemy debug_traceTransaction: batch response was not a list")
    by_id = {int(item.get("id") or 0): item for item in data if isinstance(item, dict)}
    traced: dict[str, list[dict]] = {}
    for idx, tx_hash in enumerate(tx_hashes, 1):
        item = by_id.get(idx) or {}
        if item.get("error"):
            err = item.get("error") or {}
            raise RuntimeError(
                f"Alchemy debug_traceTransaction: {err.get('code')} {str(err.get('message') or '')[:220]}"
            )
        rows: list[dict] = []
        _alchemy._trace_calls_to_wallet(
            item.get("result"),
            wallet,
            tx_hash,
            ts_by_hash.get(tx_hash, 0),
            rows,
        )
        traced[tx_hash] = _alchemy._dedupe(rows)
    return traced


def _cached_internal_rows(chain_id: int, wallet: str, required: list[str]) -> tuple[dict[str, list[dict]], list[str]]:
    key = (int(chain_id), str(wallet).lower())
    required_set = set(required)
    with _CACHE_LOCK:
        cache = _TRACE_CACHE.setdefault(key, {})
        # Drop entries no longer in this bounded history window.
        for tx_hash in list(cache):
            if tx_hash not in required_set:
                cache.pop(tx_hash, None)
        missing = [tx_hash for tx_hash in required if tx_hash not in cache]
        return dict(cache), missing


def _merge_cache(chain_id: int, wallet: str, traced: dict[str, list[dict]]) -> None:
    key = (int(chain_id), str(wallet).lower())
    with _CACHE_LOCK:
        _TRACE_CACHE.setdefault(key, {}).update(traced)


def _flatten_cached(chain_id: int, wallet: str, required: list[str]) -> list[dict]:
    key = (int(chain_id), str(wallet).lower())
    with _CACHE_LOCK:
        cache = _TRACE_CACHE.get(key, {})
        rows = [row for tx_hash in required for row in cache.get(tx_hash, [])]
    return _alchemy._dedupe(rows)


def _refresh_progressive(app, chain, wallet: str):
    url = _alchemy.alchemy_rpc_url(app, int(chain.chain_id))
    fetched_at = int(time.time())
    if not url:
        return _alchemy._store_error(
            app, chain, wallet, fetched_at,
            "Alchemy history endpoint missing from rpc_endpoints.csv",
        )

    cfg = _sibot.platform_settings(app, chain.chain_id)
    fetch_days = max(30, min(3650, _sibot._int(cfg.get("history_fetch_days"), 365)))
    cutoff = int(time.time()) - fetch_days * 86400
    max_pages = max(1, min(40, _sibot._int(cfg.get("history_max_pages"), 3)))
    page_size = max(100, min(1000, _sibot._int(cfg.get("history_page_size"), 1000)))
    delay = max(0.0, min(2.0, _sibot._float(cfg.get("history_api_delay_seconds"), 0.15)))

    try:
        outbound, c_out = _alchemy._asset_pages(
            url, wallet, "fromAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay
        )
        time.sleep(delay)
        inbound, c_in = _alchemy._asset_pages(
            url, wallet, "toAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay
        )
        transfers = _alchemy._dedupe(outbound + inbound)
        normal, _outgoing_hashes, ts_by_hash = _alchemy._tx_context(url, transfers, wallet)
        token, _ = _alchemy._normalised_transfer_rows(transfers)
        trace_hashes = _internal._trace_candidate_hashes(normal, token, wallet, _sibot._routers(app, chain))

        _cache, missing = _cached_internal_rows(chain.chain_id, wallet, trace_hashes)
        if missing:
            chunk = missing[:_TRACE_BATCH_SIZE]
            traced = _trace_chunk(url, wallet, chunk, ts_by_hash)
            _merge_cache(chain.chain_id, wallet, traced)
            _cache, missing = _cached_internal_rows(chain.chain_id, wallet, trace_hashes)

        if missing:
            completed = len(trace_hashes) - len(missing)
            return _alchemy._store_error(
                app,
                chain,
                wallet,
                fetched_at,
                f"AlchemyHistoryProgress: trace progress pending {completed}/{len(trace_hashes)}; worker yielded for cross-chain fairness",
            )

        internal = _flatten_cached(chain.chain_id, wallet, trace_hashes)
        return _alchemy._store_success(
            app,
            chain,
            wallet,
            fetched_at,
            normal,
            token,
            internal,
            bool(c_out and c_in),
        )
    except Exception as exc:
        return _alchemy._store_error(
            app,
            chain,
            wallet,
            fetched_at,
            f"AlchemyHistoryError: {type(exc).__name__}: {str(exc)[:420]}",
        )


def refresh_wallet_history(app, chain, wallet: str):
    if int(chain.chain_id) not in _TRACE_REQUIRED_CHAINS:
        return _PREV_REFRESH_WALLET_HISTORY(app, chain, wallet)
    # Preserve the account-level serialisation policy while bounding how long one
    # BSC/Arbitrum wallet can occupy it in a single worker cycle.
    with _retry._SERIAL_HISTORY_LOCK:
        return _refresh_progressive(app, chain, wallet)


def install() -> None:
    if getattr(_sibot, "_alchemy_trace_progress_patch_installed", False):
        return
    _sibot._next_history_wallet = _next_history_wallet
    _sibot.refresh_wallet_history = refresh_wallet_history
    _sibot._alchemy_trace_progress_patch_installed = True
    print(
        "[sibot-alchemy-trace-progress] bsc_arbitrum=progressive batch=4 "
        "cross_chain_fairness=true history_complete_fail_closed=true"
    )


install()
