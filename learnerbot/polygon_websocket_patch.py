from __future__ import annotations

import json
import os
import threading
import time

from websockets.sync.client import connect

from . import sibot as _sibot


# Keep the historical module name for compatibility with the existing Polygon
# runtime invariant, but this patch now provides the same WebSocket fast lane to
# all requested high-priority EVM chains.
EVM_WS_CHAINS = {
    137: {
        "slug": "polygon",
        "label": "Polygon",
        "host": "polygon-mainnet.g.alchemy.com",
        "url_envs": ("POLYGON_WS_URL",),
        "key_envs": ("POLYGON_ALCHEMY_API_KEY", "ALCHEMY_API_KEY"),
    },
    42161: {
        "slug": "arbitrum",
        "label": "Arbitrum",
        "host": "arb-mainnet.g.alchemy.com",
        "url_envs": ("ARBITRUM_WS_URL",),
        "key_envs": ("ARBITRUM_ALCHEMY_API_KEY", "ALCHEMY_API_KEY"),
    },
    56: {
        "slug": "bnb",
        "label": "BNB Chain",
        "host": "bnb-mainnet.g.alchemy.com",
        "url_envs": ("BNB_WS_URL", "BSC_WS_URL"),
        "key_envs": (
            "BNB_ALCHEMY_API_KEY",
            "BSC_ALCHEMY_API_KEY",
            "ALCHEMY_API_KEY",
        ),
    },
    8453: {
        "slug": "base",
        "label": "Base",
        "host": "base-mainnet.g.alchemy.com",
        "url_envs": ("BASE_WS_URL",),
        "key_envs": ("BASE_ALCHEMY_API_KEY", "ALCHEMY_API_KEY"),
    },
}

_START_LOCK = threading.Lock()
_LOCKS_GUARD = threading.Lock()
_POLL_LOCKS: dict[int, threading.RLock] = {}
_WS_STARTED = False
_ORIGINAL_POLL = _sibot.poll_leader_blocks
_ORIGINAL_START_WORKERS = _sibot.start_workers


def _poll_lock(chain_id: int) -> threading.RLock:
    cid = int(chain_id)
    with _LOCKS_GUARD:
        lock = _POLL_LOCKS.get(cid)
        if lock is None:
            lock = threading.RLock()
            _POLL_LOCKS[cid] = lock
        return lock


def _first_env(names) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _chain_ws_url(chain_id: int) -> str:
    """Resolve a secret-backed WSS endpoint without ever logging credentials."""
    spec = EVM_WS_CHAINS.get(int(chain_id))
    if not spec:
        return ""
    explicit = _first_env(spec["url_envs"])
    if explicit:
        return explicit
    key = _first_env(spec["key_envs"])
    if key:
        return f"wss://{spec['host']}/v2/{key}"
    return ""


def _chain(app, chain_id: int):
    for chain in _sibot.load_chains(app, enabled_only=True):
        if int(chain.chain_id) == int(chain_id):
            return chain
    return None


def poll_leader_blocks_locked(app, chain) -> list[dict]:
    # HTTP polling remains the fail-safe and receipt-validation path.  The
    # per-chain lock prevents a periodic poll and WSS event from concurrently
    # processing the same chain while allowing different chains to run in
    # parallel.
    with _poll_lock(int(chain.chain_id)):
        return _ORIGINAL_POLL(app, chain)


def _subscribe_new_heads(ws, chain_id: int) -> None:
    request_id = int(chain_id)
    ws.send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
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
        if message.get("id") != request_id:
            continue
        if message.get("error"):
            raise RuntimeError(f"EVM WebSocket subscription failed for chain {chain_id}: {message['error']}")
        if not message.get("result"):
            raise RuntimeError(f"EVM WebSocket subscription returned no id for chain {chain_id}")
        return
    raise TimeoutError(f"EVM WebSocket subscription acknowledgement timed out for chain {chain_id}")


def _chain_ws_worker(app, chain_id: int) -> None:
    spec = EVM_WS_CHAINS[int(chain_id)]
    backoff = 1.0
    while True:
        chain = _chain(app, chain_id)
        url = _chain_ws_url(chain_id)
        if chain is None or not url:
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
                _subscribe_new_heads(ws, chain_id)
                print(
                    f"[evm-ws:{spec['slug']}] connected chain={chain_id} "
                    "newHeads=true fallback_poll=true"
                )
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
                    # WSS is deliberately only the low-latency wake-up signal.
                    # Existing HTTP RPC + confirmed receipt processing remains the
                    # authoritative trading path and retains every safety gate.
                    try:
                        poll_leader_blocks_locked(app, chain)
                    except Exception as exc:
                        print(
                            f"[evm-ws:{spec['slug']}:poll]",
                            type(exc).__name__,
                            str(exc)[:180],
                        )
        except Exception as exc:
            # Do not print url: provider URLs commonly embed private API keys.
            print(f"[evm-ws:{spec['slug']}]", type(exc).__name__, str(exc)[:180])
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)


def _start_evm_ws(app) -> None:
    global _WS_STARTED
    with _START_LOCK:
        if _WS_STARTED:
            return
        _WS_STARTED = True
        for chain_id, spec in EVM_WS_CHAINS.items():
            threading.Thread(
                target=_chain_ws_worker,
                args=(app, chain_id),
                name=f"{spec['slug']}-newheads-ws",
                daemon=True,
            ).start()


def _start_polygon_ws(app) -> None:
    """Backward-compatible alias retained for existing imports/tests."""
    _start_evm_ws(app)


def start_workers_with_evm_ws(app):
    result = _ORIGINAL_START_WORKERS(app)
    _start_evm_ws(app)
    return result


def start_workers_with_polygon_ws(app):
    """Backward-compatible alias retained for the original Polygon patch API."""
    return start_workers_with_evm_ws(app)


def install() -> None:
    _sibot.poll_leader_blocks = poll_leader_blocks_locked
    _sibot.start_workers = start_workers_with_evm_ws
    chains = ",".join(str(cid) for cid in EVM_WS_CHAINS)
    print(f"[evm-ws] installed chains={chains} subscription=newHeads fallback_poll=true")


install()

# This module is already loaded late in startup by the Polygon runtime invariant,
# after the Solana reliability/cursor patches have installed.  Importing the
# Solana WSS patch here preserves that ordering without moving any trading hooks.
from . import solana_websocket_patch as _solana_ws  # noqa: E402,F401
