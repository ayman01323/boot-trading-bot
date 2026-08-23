from __future__ import annotations

import hashlib
import threading
import time
from contextlib import closing

from . import sibot as _sibot
from . import sibot_alchemy_history_patch as _alchemy

# The trace-progress layer already bounds BSC/Arbitrum debug traces, but the
# preceding transaction/receipt reconstruction can still span thousands of
# hashes and monopolise the single EVM history worker. Bound that earlier stage
# for every EVM chain as well. Partial context is never returned as complete:
# callers receive a progress exception, store history_complete=0, and yield to
# the next chain.
_CONTEXT_HASHES_PER_CYCLE = 30
_RPC_BATCH_SIZE = 10
_PROGRESS_RETRY_SECONDS = 8
_MAX_CACHE_KEYS = 64

_PREV_NEXT_HISTORY_WALLET = _sibot._next_history_wallet
_CACHE_LOCK = threading.Lock()
_CONTEXT_CACHE: dict[tuple[str, str], dict] = {}


def _endpoint_key(url: str) -> str:
    # In-memory only; never log or persist the private endpoint.
    return hashlib.sha256(str(url or "").encode("utf-8")).hexdigest()[:16]


def _metadata(transfers: list[dict]):
    hashes: list[str] = []
    ts_by_hash: dict[str, int] = {}
    block_by_hash: dict[str, str] = {}
    seen = set()
    for row in transfers:
        tx_hash = str(row.get("hash") or "").lower().strip()
        if not tx_hash:
            continue
        if tx_hash not in seen:
            seen.add(tx_hash)
            hashes.append(tx_hash)
        ts = _alchemy._timestamp(row)
        if ts:
            ts_by_hash[tx_hash] = ts
        block = str(row.get("blockNum") or "").strip()
        if block:
            block_by_hash[tx_hash] = block
    return hashes, ts_by_hash, block_by_hash


def _cache_for(url: str, wallet: str, required: list[str]) -> dict:
    key = (_endpoint_key(url), str(wallet or "").lower())
    required_set = set(required)
    now = int(time.time())
    with _CACHE_LOCK:
        cache = _CONTEXT_CACHE.setdefault(
            key,
            {
                "processed": set(),
                "tx": {},
                "receipt": {},
                "block_ts": {},
                "updated": now,
            },
        )
        cache["processed"].intersection_update(required_set)
        for name in ("tx", "receipt"):
            for tx_hash in list(cache[name]):
                if tx_hash not in required_set:
                    cache[name].pop(tx_hash, None)
        cache["updated"] = now
        if len(_CONTEXT_CACHE) > _MAX_CACHE_KEYS:
            victims = sorted(
                ((k, int(v.get("updated") or 0)) for k, v in _CONTEXT_CACHE.items() if k != key),
                key=lambda item: item[1],
            )
            for victim, _ in victims[: max(0, len(_CONTEXT_CACHE) - _MAX_CACHE_KEYS)]:
                _CONTEXT_CACHE.pop(victim, None)
        return cache


def _fetch_chunk(url: str, chunk: list[str], block_by_hash: dict[str, str], ts_by_hash: dict[str, int], cache: dict) -> None:
    if not chunk:
        return
    for start in range(0, len(chunk), _RPC_BATCH_SIZE):
        batch = chunk[start:start + _RPC_BATCH_SIZE]
        txs = _alchemy._batch_rpc(url, "eth_getTransactionByHash", [[h] for h in batch])
        time.sleep(0.15)
        receipts = _alchemy._batch_rpc(url, "eth_getTransactionReceipt", [[h] for h in batch])
        for tx_hash, tx, receipt in zip(batch, txs, receipts):
            cache["tx"][tx_hash] = tx if isinstance(tx, dict) else {}
            cache["receipt"][tx_hash] = receipt if isinstance(receipt, dict) else {}
            cache["processed"].add(tx_hash)

        needed_blocks = []
        seen_blocks = set()
        for tx_hash in batch:
            if tx_hash in ts_by_hash:
                continue
            block = block_by_hash.get(tx_hash, "")
            if block and block not in cache["block_ts"] and block not in seen_blocks:
                seen_blocks.add(block)
                needed_blocks.append(block)
        if needed_blocks:
            blocks = _alchemy._batch_rpc(
                url,
                "eth_getBlockByNumber",
                [[block, False] for block in needed_blocks],
            )
            for block, data in zip(needed_blocks, blocks):
                cache["block_ts"][block] = _alchemy._hex_int((data or {}).get("timestamp"), 0) if isinstance(data, dict) else 0


def _tx_context(url: str, transfers: list[dict], wallet: str):
    hashes, ts_by_hash, block_by_hash = _metadata(transfers)
    if not hashes:
        return [], [], ts_by_hash

    cache = _cache_for(url, wallet, hashes)
    missing = [tx_hash for tx_hash in hashes if tx_hash not in cache["processed"]]
    if missing:
        _fetch_chunk(
            url,
            missing[:_CONTEXT_HASHES_PER_CYCLE],
            block_by_hash,
            ts_by_hash,
            cache,
        )
        missing = [tx_hash for tx_hash in hashes if tx_hash not in cache["processed"]]

    if missing:
        completed = len(hashes) - len(missing)
        raise RuntimeError(
            f"AlchemyHistoryProgress: context progress pending {completed}/{len(hashes)}; worker yielded for cross-chain fairness"
        )

    normal = []
    outgoing_hashes = []
    w = str(wallet or "").lower()
    for tx_hash in hashes:
        tx = cache["tx"].get(tx_hash) or {}
        if str(tx.get("from") or "").lower() != w:
            continue
        receipt = cache["receipt"].get(tx_hash) or {}
        status = _alchemy._hex_int(receipt.get("status"), 1)
        gas_price = receipt.get("effectiveGasPrice") or tx.get("gasPrice") or "0x0"
        ts = ts_by_hash.get(tx_hash) or cache["block_ts"].get(block_by_hash.get(tx_hash, ""), 0)
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


def _progress_candidate(candidates: list[str], rows, now_epoch: int) -> str | None:
    progress = {
        str(row["wallet"] or "").lower(): int(row["fetched_at"] or 0)
        for row in rows
        if str(row["wallet"] or "").strip()
    }
    for raw in candidates:
        wallet = str(raw or "").lower()
        if wallet in progress and progress[wallet] <= now_epoch - _PROGRESS_RETRY_SECONDS:
            return wallet
    return None


def _next_history_wallet(app, chain):
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
                   WHERE chain_id=? AND error LIKE '%AlchemyHistoryProgress: context progress%'""",
                (int(chain.chain_id),),
            ).fetchall()
        chosen = _progress_candidate(candidates, rows, int(time.time()))
        if chosen:
            return chosen
    return _PREV_NEXT_HISTORY_WALLET(app, chain)


def install() -> None:
    if getattr(_alchemy, "_context_progress_patch_installed", False):
        return
    _alchemy._tx_context = _tx_context
    _sibot._next_history_wallet = _next_history_wallet
    _alchemy._context_progress_patch_installed = True
    print(
        "[sibot-alchemy-context-progress] all_evm=true hashes_per_cycle=30 "
        "cross_chain_fairness=true history_complete_fail_closed=true"
    )


install()
