from __future__ import annotations

import random
import time

import requests

from . import sibot_alchemy_history_patch as _alchemy

_MAX_ATTEMPTS = 5
_MAX_BACKOFF_SECONDS = 8.0
_RPC_BATCH_SIZE = 10
_RPC_BATCH_PAUSE_SECONDS = 0.35
_TRACE_PAUSE_SECONDS = 0.45
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def _error_code(error) -> int | None:
    if not isinstance(error, dict):
        return None
    try:
        return int(error.get("code"))
    except Exception:
        return None


def _payload_rate_limited(payload) -> bool:
    if isinstance(payload, dict):
        error = payload.get("error")
        code = _error_code(error)
        message = str((error or {}).get("message") or "").lower() if isinstance(error, dict) else ""
        return code == 429 or "compute units per second" in message or "rate limit" in message
    if isinstance(payload, list):
        return any(_payload_rate_limited(item) for item in payload if isinstance(item, dict))
    return False


def _retry_after_seconds(response, attempt: int) -> float:
    raw = ""
    try:
        raw = str((response.headers or {}).get("Retry-After") or "").strip()
    except Exception:
        raw = ""
    if raw:
        try:
            return max(0.25, min(_MAX_BACKOFF_SECONDS, float(raw)))
        except Exception:
            pass
    base = min(_MAX_BACKOFF_SECONDS, float(2 ** max(0, attempt)))
    return max(0.25, min(_MAX_BACKOFF_SECONDS, base + random.uniform(0.0, 0.25)))


def _post_json(url: str, payload, timeout: int, method_label: str):
    """POST JSON-RPC with bounded provider-aware retries and secret-safe errors.

    The private Alchemy URL is never included in raised exceptions. Read-only
    history calls remain fail-closed after the bounded retry budget is exhausted.
    """
    last_kind = "request"
    for attempt in range(_MAX_ATTEMPTS):
        response = None
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=timeout,
                headers={
                    "User-Agent": "BOOT-SiBot-Alchemy-History/1.1",
                    "Content-Type": "application/json",
                },
            )
        except requests.RequestException as exc:
            last_kind = f"transport {type(exc).__name__}"
            if attempt + 1 >= _MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Alchemy {method_label}: {last_kind}; retries exhausted"
                ) from None
            time.sleep(_retry_after_seconds(response, attempt))
            continue

        status = int(getattr(response, "status_code", 0) or 0)
        if status in _RETRYABLE_HTTP:
            last_kind = f"HTTP {status}"
            if attempt + 1 >= _MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Alchemy {method_label}: {last_kind}; retries exhausted"
                )
            time.sleep(_retry_after_seconds(response, attempt))
            continue
        if status >= 400:
            raise RuntimeError(f"Alchemy {method_label}: HTTP {status}")

        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(f"Alchemy {method_label}: invalid JSON response") from None

        if _payload_rate_limited(data):
            last_kind = "RPC 429"
            if attempt + 1 >= _MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Alchemy {method_label}: {last_kind}; retries exhausted"
                )
            time.sleep(_retry_after_seconds(response, attempt))
            continue
        return data

    raise RuntimeError(f"Alchemy {method_label}: {last_kind}; retries exhausted")


def _tx_context(url: str, transfers: list[dict], wallet: str):
    hashes: list[str] = []
    ts_by_hash: dict[str, int] = {}
    block_by_hash: dict[str, str] = {}
    for row in transfers:
        tx_hash = str(row.get("hash") or "").lower()
        if not tx_hash:
            continue
        if tx_hash not in hashes:
            hashes.append(tx_hash)
        ts = _alchemy._timestamp(row)
        if ts:
            ts_by_hash[tx_hash] = ts
        block = str(row.get("blockNum") or "").strip()
        if block:
            block_by_hash[tx_hash] = block

    tx_by_hash: dict[str, dict] = {}
    receipt_by_hash: dict[str, dict] = {}
    for start in range(0, len(hashes), _RPC_BATCH_SIZE):
        chunk = hashes[start:start + _RPC_BATCH_SIZE]
        txs = _alchemy._batch_rpc(url, "eth_getTransactionByHash", [[h] for h in chunk])
        time.sleep(_RPC_BATCH_PAUSE_SECONDS)
        receipts = _alchemy._batch_rpc(url, "eth_getTransactionReceipt", [[h] for h in chunk])
        for tx_hash, tx, receipt in zip(chunk, txs, receipts):
            if isinstance(tx, dict):
                tx_by_hash[tx_hash] = tx
            if isinstance(receipt, dict):
                receipt_by_hash[tx_hash] = receipt
        if start + _RPC_BATCH_SIZE < len(hashes):
            time.sleep(_RPC_BATCH_PAUSE_SECONDS)

    missing_blocks = sorted({
        block_by_hash[h]
        for h in hashes
        if h not in ts_by_hash and h in block_by_hash
    })
    block_ts: dict[str, int] = {}
    for start in range(0, len(missing_blocks), _RPC_BATCH_SIZE):
        chunk = missing_blocks[start:start + _RPC_BATCH_SIZE]
        blocks = _alchemy._batch_rpc(
            url,
            "eth_getBlockByNumber",
            [[block, False] for block in chunk],
        )
        for block, data in zip(chunk, blocks):
            if isinstance(data, dict):
                block_ts[block] = _alchemy._hex_int(data.get("timestamp"), 0)
        if start + _RPC_BATCH_SIZE < len(missing_blocks):
            time.sleep(_RPC_BATCH_PAUSE_SECONDS)

    normal = []
    outgoing_hashes = []
    w = wallet.lower()
    for tx_hash in hashes:
        tx = tx_by_hash.get(tx_hash) or {}
        if str(tx.get("from") or "").lower() != w:
            continue
        receipt = receipt_by_hash.get(tx_hash) or {}
        status = _alchemy._hex_int(receipt.get("status"), 1)
        gas_price = receipt.get("effectiveGasPrice") or tx.get("gasPrice") or "0x0"
        ts = ts_by_hash.get(tx_hash) or block_ts.get(block_by_hash.get(tx_hash, ""), 0)
        normal.append({
            "hash": tx_hash,
            "from": str(tx.get("from") or ""),
            "to": str(tx.get("to") or ""),
            "value": str(_alchemy._hex_int(tx.get("value"), 0)),
            "timeStamp": str(ts),
            "gasUsed": str(_alchemy._hex_int(receipt.get("gasUsed"), 0)),
            "gasPrice": str(_alchemy._hex_int(gas_price, 0)),
            "isError": "0" if status else "1",
            "txreceipt_status": "1" if status else "0",
        })
        outgoing_hashes.append(tx_hash)
    return normal, outgoing_hashes, ts_by_hash


def _trace_internal(url: str, wallet: str, tx_hashes: list[str], ts_by_hash: dict[str, int]):
    rows = []
    for idx, tx_hash in enumerate(tx_hashes):
        result = _alchemy._rpc(
            url,
            "debug_traceTransaction",
            [
                tx_hash,
                {
                    "tracer": "callTracer",
                    "tracerConfig": {"onlyTopCall": False},
                    "timeout": "20s",
                },
            ],
            timeout=45,
        )
        _alchemy._trace_calls_to_wallet(
            result,
            wallet,
            tx_hash,
            ts_by_hash.get(tx_hash, 0),
            rows,
        )
        if idx + 1 < len(tx_hashes):
            time.sleep(_TRACE_PAUSE_SECONDS)
    return _alchemy._dedupe(rows)


def install() -> None:
    if getattr(_alchemy, "_rate_limit_patch_installed", False):
        return
    _alchemy._post_json = _post_json
    _alchemy._tx_context = _tx_context
    _alchemy._trace_internal = _trace_internal
    _alchemy._rate_limit_patch_installed = True


install()
