from __future__ import annotations

import json
import os
import threading
import time
from contextlib import closing

from websockets.sync.client import connect

from . import solana_sibot as _sol


_MONITOR_LOCK = threading.RLock()
_START_LOCK = threading.Lock()
_WS_STARTED = False
_ORIGINAL_MONITOR_LEADERS = _sol.monitor_leaders
_ORIGINAL_START_WORKERS = _sol.start_workers


def _rpc_to_ws(url: str) -> str:
    value = str(url or "").strip()
    if value.startswith("https://"):
        return "wss://" + value[len("https://"):]
    if value.startswith("http://"):
        return "ws://" + value[len("http://"):]
    if value.startswith(("wss://", "ws://")):
        return value
    return ""


def _solana_ws_url(app) -> str:
    """Resolve a Solana WSS endpoint without logging embedded credentials."""
    explicit = (
        os.getenv("SOLANA_WS_URL", "").strip()
        or os.getenv("HELIUS_WS_URL", "").strip()
    )
    if explicit:
        return explicit

    helius_key = os.getenv("HELIUS_API_KEY", "").strip()
    if helius_key:
        return f"wss://mainnet.helius-rpc.com/?api-key={helius_key}"

    # Reuse the configured Solana RPC provider if it exposes the standard
    # matching WebSocket endpoint.  Dedicated Helius is preferred in production.
    try:
        rpc_url = str(_sol.settings(app).get("rpc_url") or "").strip()
    except Exception:
        rpc_url = ""
    return _rpc_to_ws(rpc_url)


def _selected_leaders(app) -> list[str]:
    try:
        with closing(_sol.connect(app)) as conn:
            rows = conn.execute(
                "SELECT DISTINCT wallet FROM leaders ORDER BY wallet"
            ).fetchall()
        return [str(r["wallet"] or "").strip() for r in rows if str(r["wallet"] or "").strip()]
    except Exception:
        return []


def monitor_leaders_locked(app):
    # The periodic Solana polling worker remains active as a reliability fallback.
    # This lock prevents it racing a WSS-triggered leader pass.
    with _MONITOR_LOCK:
        return _ORIGINAL_MONITOR_LEADERS(app)


def _subscribe_leaders(ws, leaders: list[str]) -> dict[str, str]:
    """Subscribe to confirmed logs mentioning each current leader wallet."""
    if not leaders:
        return {}

    pending: dict[int, str] = {}
    request_id = 1000
    for wallet in leaders:
        request_id += 1
        pending[request_id] = wallet
        ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [wallet]},
                        {"commitment": "confirmed"},
                    ],
                },
                separators=(",", ":"),
            )
        )

    subscriptions: dict[str, str] = {}
    deadline = time.monotonic() + 15.0
    while pending and time.monotonic() < deadline:
        raw = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
        message = json.loads(raw)
        msg_id = message.get("id")
        if msg_id not in pending:
            # A notification can arrive while other subscription acknowledgements
            # are pending.  The normal 5-second polling fallback covers this tiny
            # startup window, so no durable cursor is advanced here.
            continue
        wallet = pending.pop(msg_id)
        if message.get("error"):
            raise RuntimeError(
                f"Solana logsSubscribe failed for leader {wallet[:8]}: {message['error']}"
            )
        sub_id = message.get("result")
        if sub_id is None:
            raise RuntimeError("Solana logsSubscribe returned no subscription id")
        subscriptions[str(sub_id)] = wallet

    if pending:
        raise TimeoutError("Solana leader WebSocket subscription acknowledgement timed out")
    return subscriptions


def _solana_ws_worker(app) -> None:
    backoff = 1.0
    while True:
        url = _solana_ws_url(app)
        leaders = _selected_leaders(app)
        if not url or not leaders:
            time.sleep(5)
            continue

        try:
            with connect(
                url,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
            ) as ws:
                subscriptions = _subscribe_leaders(ws, leaders)
                subscribed_set = set(leaders)
                print(
                    f"[solana-ws] connected logsSubscribe=true leaders={len(subscriptions)} "
                    "commitment=confirmed fallback_poll=true"
                )
                backoff = 1.0
                last_refresh = time.monotonic()

                while True:
                    # Reconnect to refresh the subscription set when the Strategy
                    # engine changes selected leaders.  This avoids stale wallet
                    # streams without adding subscription mutation complexity.
                    if time.monotonic() - last_refresh >= 10.0:
                        current = set(_selected_leaders(app))
                        if current != subscribed_set:
                            break
                        last_refresh = time.monotonic()

                    try:
                        raw = ws.recv(timeout=2.0)
                    except TimeoutError:
                        continue
                    try:
                        message = json.loads(raw)
                    except Exception:
                        continue
                    if message.get("method") != "logsNotification":
                        continue
                    params = message.get("params") or {}
                    result = params.get("result") or {}
                    value = result.get("value") or {}
                    if value.get("err") is not None:
                        continue

                    # A leader produced a confirmed on-chain transaction.  Wake
                    # the existing cursor-safe leader monitor immediately; it
                    # still fetches/classifies the transaction through the proven
                    # HTTP RPC path and preserves every freshness/preflight gate.
                    try:
                        monitor_leaders_locked(app)
                    except Exception as exc:
                        print(
                            "[solana-ws:monitor]",
                            type(exc).__name__,
                            str(exc)[:180],
                        )
        except Exception as exc:
            # Provider URLs often include API keys; never print the URL.
            print("[solana-ws]", type(exc).__name__, str(exc)[:180])
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)


def _start_solana_ws(app) -> None:
    global _WS_STARTED
    with _START_LOCK:
        if _WS_STARTED:
            return
        _WS_STARTED = True
        threading.Thread(
            target=_solana_ws_worker,
            args=(app,),
            name="solana-leader-logs-ws",
            daemon=True,
        ).start()


def start_workers_with_solana_ws(app):
    result = _ORIGINAL_START_WORKERS(app)
    _start_solana_ws(app)
    return result


def install() -> None:
    _sol.monitor_leaders = monitor_leaders_locked
    _sol.start_workers = start_workers_with_solana_ws
    print(
        "[solana-ws] installed subscription=logsSubscribe commitment=confirmed "
        "fallback_poll=true"
    )


install()
