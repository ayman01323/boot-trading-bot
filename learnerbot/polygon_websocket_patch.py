from __future__ import annotations

import json
import os
import threading
import time

from websockets.sync.client import connect

from . import sibot as _sibot


POLYGON_CHAIN_ID = 137
_POLL_LOCK = threading.RLock()
_START_LOCK = threading.Lock()
_WS_STARTED = False
_ORIGINAL_POLL = _sibot.poll_leader_blocks
_ORIGINAL_START_WORKERS = _sibot.start_workers


def _polygon_ws_url() -> str:
    """Return a secret-backed Polygon WSS endpoint without logging credentials."""
    explicit = os.getenv("POLYGON_WS_URL", "").strip()
    if explicit:
        return explicit
    key = (
        os.getenv("POLYGON_ALCHEMY_API_KEY", "").strip()
        or os.getenv("ALCHEMY_API_KEY", "").strip()
    )
    if key:
        return f"wss://polygon-mainnet.g.alchemy.com/v2/{key}"
    return ""


def _polygon_chain(app):
    for chain in _sibot.load_chains(app, enabled_only=True):
        if int(chain.chain_id) == POLYGON_CHAIN_ID:
            return chain
    return None


def poll_leader_blocks_locked(app, chain) -> list[dict]:
    # The normal 3-second monitor remains as a fallback.  The lock prevents the
    # periodic poll and a WebSocket-triggered poll from processing the same
    # Polygon head concurrently.
    with _POLL_LOCK:
        return _ORIGINAL_POLL(app, chain)


def _subscribe_new_heads(ws) -> None:
    ws.send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 137,
                "method": "eth_subscribe",
                "params": ["newHeads"],
            },
            separators=(",", ":"),
        )
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        raw = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
        message = json.loads(raw)
        if message.get("id") != 137:
            continue
        if message.get("error"):
            raise RuntimeError(f"Polygon WebSocket subscription failed: {message['error']}")
        if not message.get("result"):
            raise RuntimeError("Polygon WebSocket subscription returned no subscription id")
        return
    raise TimeoutError("Polygon WebSocket subscription acknowledgement timed out")


def _polygon_ws_worker(app) -> None:
    backoff = 1.0
    while True:
        chain = _polygon_chain(app)
        url = _polygon_ws_url()
        if chain is None or not url:
            # Configuration can be added without restarting the process forever;
            # re-check periodically while leaving HTTP polling fully operational.
            time.sleep(15)
            continue
        try:
            with connect(
                url,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=2 * 1024 * 1024,
            ) as ws:
                _subscribe_new_heads(ws)
                print("[polygon-ws] connected newHeads=true fallback_poll=true")
                backoff = 1.0
                for raw in ws:
                    try:
                        message = json.loads(raw)
                    except Exception:
                        continue
                    if message.get("method") != "eth_subscription":
                        continue
                    result = (message.get("params") or {}).get("result") or {}
                    if not result.get("number"):
                        continue
                    # Preserve the existing reliability/receipt semantics: WSS
                    # only wakes the monitor immediately; the proven HTTP RPC
                    # path still fetches the block and confirms tx receipts.
                    try:
                        poll_leader_blocks_locked(app, chain)
                    except Exception as exc:
                        print(
                            "[polygon-ws:poll]",
                            type(exc).__name__,
                            str(exc)[:180],
                        )
        except Exception as exc:
            # Never print the WSS URL because it can contain the provider key.
            print("[polygon-ws]", type(exc).__name__, str(exc)[:180])
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)


def _start_polygon_ws(app) -> None:
    global _WS_STARTED
    with _START_LOCK:
        if _WS_STARTED:
            return
        _WS_STARTED = True
        thread = threading.Thread(
            target=_polygon_ws_worker,
            args=(app,),
            name="polygon-newheads-ws",
            daemon=True,
        )
        thread.start()


def start_workers_with_polygon_ws(app):
    result = _ORIGINAL_START_WORKERS(app)
    _start_polygon_ws(app)
    return result


def install() -> None:
    # Wrap the already-installed reliable EVM poller, so both the existing
    # periodic path and the Polygon WSS trigger share the same serialisation.
    _sibot.poll_leader_blocks = poll_leader_blocks_locked
    _sibot.start_workers = start_workers_with_polygon_ws
    print("[polygon-ws] installed chain=137 subscription=newHeads")


install()
