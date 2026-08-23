from __future__ import annotations

import json
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
#
# Cost control matters here: Alchemy transfer/history context is substantially
# more expensive than reusing already-fetched context. A wallet that still has
# trace work pending therefore waits several minutes rather than being selected
# every few seconds, and the transfer/transaction context is reused across trace
# chunks within the running service.
_TRACE_REQUIRED_CHAINS = {56, 42161}
_TRACE_BATCH_SIZE = 4
_PROGRESS_RETRY_SECONDS = 180
_CONTEXT_TTL_SECONDS = 900

_PREV_REFRESH_WALLET_HISTORY = _sibot.refresh_wallet_history
_PREV_NEXT_HISTORY_WALLET = _sibot._next_history_wallet
_CACHE_LOCK = threading.Lock()
_TRACE_CACHE: dict[tuple[int, str], dict[str, list[dict]]] = {}
_CONTEXT_CACHE: dict[tuple[int, str], dict] = {}


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


def _ensure_persistent_trace_cache(app) -> None:
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS alchemy_trace_cache (
                   chain_id INTEGER NOT NULL,
                   wallet TEXT NOT NULL,
                   tx_hash TEXT NOT NULL,
                   rows_json TEXT NOT NULL,
                   updated_at INTEGER NOT NULL,
                   PRIMARY KEY(chain_id,wallet,tx_hash)
               )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alchemy_trace_cache_wallet "
            "ON alchemy_trace_cache(chain_id,wallet,updated_at)"
        )
        conn.commit()


def _load_persistent_trace_cache(app, chain_id: int, wallet: str) -> dict[str, list[dict]]:
    _ensure_persistent_trace_cache(app)
    out: dict[str, list[dict]] = {}
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        rows = conn.execute(
            "SELECT tx_hash,rows_json FROM alchemy_trace_cache WHERE chain_id=? AND wallet=?",
            (int(chain_id), str(wallet).lower()),
        ).fetchall()
    for row in rows:
        try:
            parsed = json.loads(str(row["rows_json"] or "[]"))
            out[str(row["tx_hash"])] = list(parsed) if isinstance(parsed, list) else []
        except Exception:
            continue
    return out


def _save_persistent_trace_cache(app, chain_id: int, wallet: str, traced: dict[str, list[dict]]) -> None:
    if not traced:
        return
    _ensure_persistent_trace_cache(app)
    now = int(time.time())
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        for tx_hash, rows in traced.items():
            conn.execute(
                """INSERT INTO alchemy_trace_cache(chain_id,wallet,tx_hash,rows_json,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(chain_id,wallet,tx_hash) DO UPDATE SET
                     rows_json=excluded.rows_json,updated_at=excluded.updated_at""",
                (
                    int(chain_id),
                    str(wallet).lower(),
                    str(tx_hash),
                    json.dumps(list(rows or []), separators=(",", ":"), sort_keys=True),
                    now,
                ),
            )
        conn.commit()


def _cached_internal_rows(app, chain_id: int, wallet: str, required: list[str]) -> tuple[dict[str, list[dict]], list[str]]:
    key = (int(chain_id), str(wallet).lower())
    required_set = set(required)
    persistent = _load_persistent_trace_cache(app, chain_id, wallet) if required else {}
    with _CACHE_LOCK:
        cache = _TRACE_CACHE.setdefault(key, {})
        for tx_hash, rows in persistent.items():
            if tx_hash in required_set and tx_hash not in cache:
                cache[tx_hash] = rows
        # Drop in-memory entries no longer in this bounded history window. The
        # durable table remains as a restart cache for immutable confirmed txs.
        for tx_hash in list(cache):
            if tx_hash not in required_set:
                cache.pop(tx_hash, None)
        missing = [tx_hash for tx_hash in required if tx_hash not in cache]
        return dict(cache), missing


def _merge_cache(app, chain_id: int, wallet: str, traced: dict[str, list[dict]]) -> None:
    key = (int(chain_id), str(wallet).lower())
    with _CACHE_LOCK:
        _TRACE_CACHE.setdefault(key, {}).update(traced)
    _save_persistent_trace_cache(app, chain_id, wallet, traced)


def _flatten_cached(app, chain_id: int, wallet: str, required: list[str]) -> list[dict]:
    cache, _missing = _cached_internal_rows(app, chain_id, wallet, required)
    rows = [row for tx_hash in required for row in cache.get(tx_hash, [])]
    return _alchemy._dedupe(rows)


def _context_key(chain_id: int, wallet: str) -> tuple[int, str]:
    return int(chain_id), str(wallet).lower()


def _get_context(chain_id: int, wallet: str) -> dict | None:
    key = _context_key(chain_id, wallet)
    now = time.monotonic()
    with _CACHE_LOCK:
        row = _CONTEXT_CACHE.get(key)
        if not row:
            return None
        if float(row.get("expires_monotonic") or 0) <= now:
            _CONTEXT_CACHE.pop(key, None)
            return None
        return dict(row)


def _set_context(chain_id: int, wallet: str, row: dict, ttl_seconds: int) -> dict:
    value = dict(row)
    value["expires_monotonic"] = time.monotonic() + max(120, min(3600, int(ttl_seconds)))
    with _CACHE_LOCK:
        _CONTEXT_CACHE[_context_key(chain_id, wallet)] = value
    return dict(value)


def _clear_context(chain_id: int, wallet: str) -> None:
    with _CACHE_LOCK:
        _CONTEXT_CACHE.pop(_context_key(chain_id, wallet), None)


def _build_context(app, chain, wallet: str, url: str, cfg: dict) -> dict:
    fetch_days = max(30, min(3650, _sibot._int(cfg.get("history_fetch_days"), 365)))
    cutoff = int(time.time()) - fetch_days * 86400
    max_pages = max(1, min(40, _sibot._int(cfg.get("history_max_pages"), 3)))
    page_size = max(100, min(1000, _sibot._int(cfg.get("history_page_size"), 1000)))
    delay = max(0.0, min(2.0, _sibot._float(cfg.get("history_api_delay_seconds"), 0.15)))

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
    return {
        "normal": normal,
        "token": token,
        "trace_hashes": list(trace_hashes),
        "ts_by_hash": dict(ts_by_hash),
        "complete": bool(c_out and c_in),
    }


def _refresh_progressive(app, chain, wallet: str):
    url = _alchemy.alchemy_rpc_url(app, int(chain.chain_id))
    fetched_at = int(time.time())
    if not url:
        return _alchemy._store_error(
            app, chain, wallet, fetched_at,
            "Alchemy history endpoint missing from rpc_endpoints.csv",
        )

    cfg = _sibot.platform_settings(app, chain.chain_id)
    context_ttl = max(
        120,
        min(3600, _sibot._int(cfg.get("history_progress_context_ttl_seconds"), _CONTEXT_TTL_SECONDS)),
    )

    try:
        context = _get_context(chain.chain_id, wallet)
        if context is None:
            context = _set_context(
                chain.chain_id,
                wallet,
                _build_context(app, chain, wallet, url, cfg),
                context_ttl,
            )

        normal = list(context.get("normal") or [])
        token = list(context.get("token") or [])
        trace_hashes = list(context.get("trace_hashes") or [])
        ts_by_hash = dict(context.get("ts_by_hash") or {})

        _cache, missing = _cached_internal_rows(app, chain.chain_id, wallet, trace_hashes)
        if missing:
            chunk = missing[:_TRACE_BATCH_SIZE]
            traced = _trace_chunk(url, wallet, chunk, ts_by_hash)
            _merge_cache(app, chain.chain_id, wallet, traced)
            _cache, missing = _cached_internal_rows(app, chain.chain_id, wallet, trace_hashes)

        if missing:
            completed = len(trace_hashes) - len(missing)
            return _alchemy._store_error(
                app,
                chain,
                wallet,
                fetched_at,
                f"AlchemyHistoryProgress: trace progress pending {completed}/{len(trace_hashes)}; "
                f"worker yielded for cross-chain fairness and RPC-cost control",
            )

        internal = _flatten_cached(app, chain.chain_id, wallet, trace_hashes)
        result = _alchemy._store_success(
            app,
            chain,
            wallet,
            fetched_at,
            normal,
            token,
            internal,
            bool(context.get("complete")),
        )
        _clear_context(chain.chain_id, wallet)
        return result
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
        "retry=180s context_cache=900s persistent_trace_cache=true "
        "cross_chain_fairness=true history_complete_fail_closed=true"
    )


install()
