from __future__ import annotations

import csv
import json
import threading
import time

from websockets.sync.client import connect

from . import sibot as _sibot


# Keep the historical module name for compatibility with the existing Polygon
# runtime invariant, but this patch now provides the same WebSocket fast lane to
# all requested high-priority EVM chains. Provider credentials live only in the
# VPS runtime CSV, never in environment variables or this repository.
EVM_WS_CHAINS = {
    137: {"slug": "polygon", "label": "Polygon"},
    42161: {"slug": "arbitrum", "label": "Arbitrum"},
    56: {"slug": "bnb", "label": "BNB Chain"},
    8453: {"slug": "base", "label": "Base"},
}

# Alchemy currently supports alchemy_minedTransactions on Polygon and Arbitrum
# among the chains used here. BNB and Base retain newHeads plus the same proven
# HTTP cursor/receipt path rather than assuming unsupported provider features.
_FILTERED_MINED_CHAINS = {137, 42161}
_MAX_FILTER_ADDRESSES = 1000

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


def _bool(value, default=True) -> bool:
    if value is None or str(value).strip() == "":
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _csv_ws_value(value: str) -> str:
    """Accept only a complete WebSocket URL stored directly in the runtime CSV."""
    text = str(value or "").strip()
    if not text:
        return ""
    if "$" in text:
        return ""
    return text if text.startswith(("wss://", "ws://")) else ""


def _csv_ws_url(app, chain_id: int) -> str:
    """Return highest-priority enabled ws_url from CSVbot/rpc_endpoints.csv."""
    if app is None:
        return ""
    path = getattr(app, "csv_dir", None)
    if path is None:
        return ""
    path = path / "rpc_endpoints.csv"
    if not path.exists():
        return ""

    candidates: list[tuple[int, str]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if not _bool(row.get("enabled"), True):
                    continue
                try:
                    cid = int(str(row.get("chain_id") or "0").strip())
                except Exception:
                    continue
                if cid != int(chain_id):
                    continue
                raw_url = str(row.get("ws_url") or "").strip() or str(row.get("websocket_url") or "").strip()
                url = _csv_ws_value(raw_url)
                if not url:
                    continue
                try:
                    priority = int(str(row.get("priority") or "999").strip())
                except Exception:
                    priority = 999
                candidates.append((priority, url))
    except Exception:
        return ""

    return sorted(candidates, key=lambda item: item[0])[0][1] if candidates else ""


def _chain_ws_url(chain_id: int, app=None) -> str:
    """Resolve EVM WebSocket URL exclusively from rpc_endpoints.csv."""
    if int(chain_id) not in EVM_WS_CHAINS:
        return ""
    return _csv_ws_url(app, chain_id)


def _chain(app, chain_id: int):
    for chain in _sibot.load_chains(app, enabled_only=True):
        if int(chain.chain_id) == int(chain_id):
            return chain
    return None


def _leader_addresses(app, chain_id: int) -> list[str]:
    try:
        leaders = _sibot._leader_set(app, int(chain_id))
    except Exception:
        return []
    return sorted({str(value or "").lower() for value in leaders if str(value or "").strip()})[:_MAX_FILTER_ADDRESSES]


def poll_leader_blocks_locked(app, chain) -> list[dict]:
    # HTTP polling remains the fail-safe and receipt-validation path. The
    # per-chain lock prevents a periodic poll and WSS event from concurrently
    # processing the same chain while allowing different chains to run in
    # parallel.
    with _poll_lock(int(chain.chain_id)):
        return _ORIGINAL_POLL(app, chain)


def _subscription_request(chain_id: int, url: str, leaders: list[str]) -> tuple[list, str]:
    filtered = (
        int(chain_id) in _FILTERED_MINED_CHAINS
        and "alchemy.com" in str(url or "").lower()
        and bool(leaders)
    )
    if filtered:
        return [
            "alchemy_minedTransactions",
            {
                "addresses": [{"from": address} for address in leaders],
                "includeRemoved": False,
                "hashesOnly": True,
            },
        ], "leader_mined_transactions"
    return ["newHeads"], "new_heads"


def _subscribe(ws, chain_id: int, url: str, leaders: list[str]) -> str:
    request_id = int(chain_id)
    params, mode = _subscription_request(chain_id, url, leaders)
    ws.send(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": "eth_subscribe", "params": params},
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
        return mode
    raise TimeoutError(f"EVM WebSocket subscription acknowledgement timed out for chain {chain_id}")


def _notification_should_wake(message: dict, mode: str) -> bool:
    if message.get("method") != "eth_subscription":
        return False
    result = (message.get("params") or {}).get("result") or {}
    if mode == "leader_mined_transactions":
        if bool(result.get("removed")):
            return False
        tx = result.get("transaction") or {}
        return bool(str(tx.get("hash") or "").strip())
    return bool(result.get("number"))


def _chain_ws_worker(app, chain_id: int) -> None:
    spec = EVM_WS_CHAINS[int(chain_id)]
    backoff = 1.0
    while True:
        chain = _chain(app, chain_id)
        url = _chain_ws_url(chain_id, app)
        if chain is None or not url:
            time.sleep(15)
            continue
        leaders = _leader_addresses(app, chain_id)
        try:
            with connect(
                url,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=2 * 1024 * 1024,
            ) as ws:
                mode = _subscribe(ws, chain_id, url, leaders)
                print(
                    f"[evm-ws:{spec['slug']}] connected chain={chain_id} mode={mode} "
                    "source=rpc_endpoints_csv fallback_poll=true"
                )
                backoff = 1.0
                while True:
                    if _chain_ws_url(chain_id, app) != url:
                        print(f"[evm-ws:{spec['slug']}] endpoint_changed reconnect=true")
                        break
                    if mode == "leader_mined_transactions" and _leader_addresses(app, chain_id) != leaders:
                        print(f"[evm-ws:{spec['slug']}] leaders_changed reconnect=true")
                        break
                    try:
                        raw = ws.recv(timeout=10.0)
                    except TimeoutError:
                        continue
                    try:
                        message = json.loads(raw)
                    except Exception:
                        continue
                    if not _notification_should_wake(message, mode):
                        continue
                    # WSS is only the low-latency wake-up signal. Existing HTTP
                    # RPC + confirmed receipt processing remains authoritative and
                    # retains every execution, reliability and safety gate.
                    try:
                        poll_leader_blocks_locked(app, chain)
                    except Exception as exc:
                        print(f"[evm-ws:{spec['slug']}:poll]", type(exc).__name__, str(exc)[:180])
        except Exception as exc:
            # Do not print url: provider URLs embed the private API key in CSV.
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
                name=f"{spec['slug']}-evm-ws",
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
    print(
        f"[evm-ws] installed chains={chains} polygon_arbitrum=leader_mined_transactions "
        "bnb_base=newHeads source=rpc_endpoints_csv_only fallback_poll=true"
    )


install()

# This module is already loaded late in startup by the Polygon runtime invariant,
# after the Solana reliability/cursor patches have installed. Importing the
# Solana WSS patch here preserves that ordering without moving any trading hooks.
from . import solana_websocket_patch as _solana_ws  # noqa: E402,F401
