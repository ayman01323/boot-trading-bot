from __future__ import annotations

"""Reduce Alchemy history RPC burst pressure without weakening reconstruction.

The original history context stage fetched transaction + receipt data for every hash
present in external/ERC20 transfer pages. SiBot spot reconstruction can only use a
transaction when the wallet itself has an ERC20 transfer in that hash. Fetching
context for unrelated hashes wastes provider compute units and was a major source of
HTTP 429 pressure on large histories.

This patch keeps every swap-relevant hash, preserves the same transaction/receipt
fields and timestamps, and uses smaller batches with a short inter-batch delay. It
changes history/research plumbing only: no LIVE/AUTO/ARMED, signing, capital,
quality, profit, liquidity or execution gate is changed.
"""

import time

from . import sibot_alchemy_history_patch as _alchemy

_PREV_TX_CONTEXT = _alchemy._tx_context
_BATCH_SIZE = 20
_BATCH_DELAY_SECONDS = 0.12


def _candidate_hashes(transfers, wallet: str) -> list[str]:
    """Return hashes that can possibly contribute to a spot-trade reconstruction."""
    w = str(wallet or "").lower()
    out: list[str] = []
    seen = set()
    for row in transfers:
        if str(row.get("category") or "").lower() != "erc20":
            continue
        frm = str(row.get("from") or "").lower()
        to = str(row.get("to") or "").lower()
        if w not in {frm, to}:
            continue
        if _alchemy._raw_transfer_value(row) <= 0:
            continue
        tx_hash = str(row.get("hash") or "").lower().strip()
        if not tx_hash or tx_hash in seen:
            continue
        seen.add(tx_hash)
        out.append(tx_hash)
    return out


def tx_context_swap_relevant(url: str, transfers, wallet: str):
    """Fetch full tx/receipt context only for hashes relevant to spot reconstruction."""
    hashes = _candidate_hashes(transfers, wallet)
    ts_by_hash = {}
    block_by_hash = {}
    wanted = set(hashes)
    for row in transfers:
        tx_hash = str(row.get("hash") or "").lower()
        if tx_hash not in wanted:
            continue
        ts = _alchemy._timestamp(row)
        if ts:
            ts_by_hash[tx_hash] = ts
        block = str(row.get("blockNum") or "").strip()
        if block:
            block_by_hash[tx_hash] = block

    tx_by_hash = {}
    receipt_by_hash = {}
    for start in range(0, len(hashes), _BATCH_SIZE):
        chunk = hashes[start:start + _BATCH_SIZE]
        txs = _alchemy._batch_rpc(url, "eth_getTransactionByHash", [[h] for h in chunk])
        if _BATCH_DELAY_SECONDS:
            time.sleep(_BATCH_DELAY_SECONDS)
        receipts = _alchemy._batch_rpc(url, "eth_getTransactionReceipt", [[h] for h in chunk])
        for tx_hash, tx, receipt in zip(chunk, txs, receipts):
            if isinstance(tx, dict):
                tx_by_hash[tx_hash] = tx
            if isinstance(receipt, dict):
                receipt_by_hash[tx_hash] = receipt
        if _BATCH_DELAY_SECONDS and start + _BATCH_SIZE < len(hashes):
            time.sleep(_BATCH_DELAY_SECONDS)

    missing_blocks = sorted({
        block_by_hash[h] for h in hashes if h not in ts_by_hash and h in block_by_hash
    })
    block_ts = {}
    for start in range(0, len(missing_blocks), _BATCH_SIZE):
        chunk = missing_blocks[start:start + _BATCH_SIZE]
        blocks = _alchemy._batch_rpc(url, "eth_getBlockByNumber", [[block, False] for block in chunk])
        for block, data in zip(chunk, blocks):
            if isinstance(data, dict):
                block_ts[block] = _alchemy._hex_int(data.get("timestamp"), 0)
        if _BATCH_DELAY_SECONDS and start + _BATCH_SIZE < len(missing_blocks):
            time.sleep(_BATCH_DELAY_SECONDS)

    normal = []
    outgoing_hashes = []
    w = str(wallet or "").lower()
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


def install() -> None:
    if getattr(_alchemy, "_context_rate_patch_installed", False):
        return
    _alchemy._tx_context = tx_context_swap_relevant
    _alchemy._context_rate_patch_installed = True
    print(
        "[sibot-alchemy-context-rate] installed=true swap_relevant_hashes_only=true "
        "batch_size=20 paced=true history_semantics=unchanged execution_safety=unchanged",
        flush=True,
    )


install()
