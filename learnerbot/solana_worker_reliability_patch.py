from __future__ import annotations

import threading
import time
from contextlib import closing

from . import solana_sibot as _sol

_PREV_RPC = _sol._rpc

_sol.DEFAULTS.update({
    "rpc_retry_attempts": ("3", "Bounded retries for transient Solana RPC failures"),
    "rpc_retry_backoff_seconds": ("0.75", "Base retry delay for transient Solana RPC failures"),
    "history_worker_seconds": ("2", "Pause between independent Solana history-backfill jobs"),
})


def _transient(exc: Exception) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in {408, 425, 429, 500, 502, 503, 504}:
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if any(x in name for x in ("timeout", "connection", "http")):
        return True
    return any(x in text for x in (
        "429", "rate limit", "too many requests", "timeout", "timed out",
        "temporarily unavailable", "service unavailable", "connection reset",
        "connection aborted", "gateway timeout", "node is behind",
    ))


def rpc_with_retry(app, method: str, params: list):
    cfg = _sol.settings(app)
    attempts = max(1, min(5, _sol._int(cfg.get("rpc_retry_attempts"), 3)))
    backoff = max(0.10, min(5.0, _sol._float(cfg.get("rpc_retry_backoff_seconds"), 0.75)))
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return _PREV_RPC(app, method, params)
        except Exception as exc:
            last = exc
            if attempt >= attempts or not _transient(exc):
                raise
            time.sleep(backoff * attempt)
    raise last  # pragma: no cover


def discover_recent_blocks_reliable(app) -> int:
    """Discover recent swaps without advancing past an RPC-failed block."""
    cfg = _sol.settings(app)
    if not _sol._bool(cfg.get("enabled"), True):
        return 0
    latest = int(_sol._rpc(app, "getSlot", [{"commitment": "finalized"}]) or 0)
    blocks_per = max(1, min(10, _sol._int(cfg.get("discovery_blocks_per_cycle"), 2)))
    with closing(_sol.connect(app)) as conn:
        last = _sol._int(_sol._state(conn, "last_discovery_slot", 0), 0)
        if last <= 0:
            last = max(0, latest - 6)
        if latest - last > 100:
            last = max(0, latest - 50)
        start = last + 1
        end = min(latest, start + blocks_per - 1)

    found = 0
    processed = last
    for slot in range(start, end + 1):
        try:
            block = _sol._rpc(app, "getBlock", [slot, {
                "commitment": "finalized",
                "encoding": "jsonParsed",
                "transactionDetails": "full",
                "rewards": False,
                "maxSupportedTransactionVersion": 0,
            }])
        except Exception as exc:
            print("[sibot-solana-discovery-slot]", slot, type(exc).__name__, str(exc)[:180])
            break

        processed = slot
        if not block:
            continue
        block_time = int(block.get("blockTime") or time.time())
        for item in block.get("transactions") or []:
            result = {
                "slot": slot,
                "blockTime": block_time,
                "transaction": item.get("transaction") or {},
                "meta": item.get("meta") or {},
            }
            if result["meta"].get("err") is not None or not _sol._looks_like_swap(result):
                continue
            for wallet in _sol._signers(result)[:2]:
                event = _sol.classify_swap(result, wallet)
                if not event:
                    continue
                now = int(time.time())
                with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
                    conn.execute(
                        """INSERT INTO candidates(wallet,first_seen,last_seen,swap_events,last_signature,updated_at)
                           VALUES(?,?,?,?,?,?)
                           ON CONFLICT(wallet) DO UPDATE SET last_seen=excluded.last_seen,
                             swap_events=candidates.swap_events+1,last_signature=excluded.last_signature,updated_at=excluded.updated_at""",
                        (wallet, event["event_ts"], event["event_ts"], 1, event["signature"], now),
                    )
                    conn.commit()
                found += 1

    if processed > last:
        with closing(_sol.connect(app)) as conn:
            _sol._set_state(conn, "last_discovery_slot", processed)
    return found


def _mark(app, worker: str, *, ok: bool, error: str = ""):
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            _sol._set_state(conn, f"worker:{worker}:last_run", int(time.time()))
            if ok:
                _sol._set_state(conn, f"worker:{worker}:last_success", int(time.time()))
                _sol._set_state(conn, f"worker:{worker}:last_error", "")
            elif error:
                _sol._set_state(conn, f"worker:{worker}:last_error", str(error)[:500])
    except Exception:
        pass


def _discovery_worker(app):
    while True:
        cfg = _sol.settings(app)
        if _sol._bool(cfg.get("enabled"), True):
            try:
                discover_recent_blocks_reliable(app)
                _sol.refresh_rankings(app)
                _mark(app, "discovery", ok=True)
            except Exception as exc:
                _mark(app, "discovery", ok=False, error=f"{type(exc).__name__}: {exc}")
                print("[sibot-solana-discovery]", type(exc).__name__, exc)
        time.sleep(max(5, _sol._int(cfg.get("discovery_interval_seconds"), 15)))


def _history_worker(app):
    while True:
        cfg = _sol.settings(app)
        delay = max(1, _sol._int(cfg.get("history_worker_seconds"), 2))
        if _sol._bool(cfg.get("enabled"), True):
            try:
                wallet = _sol._next_history_wallet(app)
                if wallet:
                    result = _sol.refresh_wallet_history(app, wallet)
                    if result.get("error"):
                        raise RuntimeError(result["error"])
                _mark(app, "history", ok=True)
            except Exception as exc:
                _mark(app, "history", ok=False, error=f"{type(exc).__name__}: {exc}")
                print("[sibot-solana-history]", type(exc).__name__, exc)
        time.sleep(delay)


def _leader_worker(app):
    last_position = 0
    while True:
        cfg = _sol.settings(app)
        if _sol._bool(cfg.get("enabled"), True):
            ok = True
            errors = []
            try:
                _sol.monitor_leaders(app)
            except Exception as exc:
                ok = False
                errors.append(f"leaders {type(exc).__name__}: {exc}")
                print("[sibot-solana-leaders]", type(exc).__name__, exc)
            now = int(time.time())
            if now - last_position >= max(10, _sol._int(cfg.get("position_poll_seconds"), 15)):
                try:
                    _sol.monitor_positions(app)
                except Exception as exc:
                    ok = False
                    errors.append(f"positions {type(exc).__name__}: {exc}")
                    print("[sibot-solana-positions]", type(exc).__name__, exc)
                last_position = now
            _mark(app, "leader", ok=ok, error=" | ".join(errors))
        time.sleep(max(3, _sol._int(cfg.get("leader_poll_seconds"), 5)))


def start_workers_reliable(app):
    with _sol._WORKER_LOCK:
        if _sol._WORKER_STARTED:
            return
        _sol._WORKER_STARTED = True
    _sol.ensure_settings(app)
    _sol.connect(app).close()
    threading.Thread(
        target=_discovery_worker, args=(app,), daemon=True, name="sibot-solana-discovery"
    ).start()
    threading.Thread(
        target=_history_worker, args=(app,), daemon=True, name="sibot-solana-history"
    ).start()
    threading.Thread(
        target=_leader_worker, args=(app,), daemon=True, name="sibot-solana-leaders"
    ).start()
    print(
        "[sibot-solana] retry-safe discovery + independent history backfill + leader/position monitoring started"
    )


def install():
    _sol._rpc = rpc_with_retry
    _sol.discover_recent_blocks = discover_recent_blocks_reliable
    _sol.start_workers = start_workers_reliable
    print(
        "[solana-worker-reliability] rpc_retries=true no_slot_skip=true "
        "history_independent=true leader_heartbeat=true"
    )


install()
