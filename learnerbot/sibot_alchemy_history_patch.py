from __future__ import annotations

import csv
import datetime as dt
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import requests
from web3 import Web3

from . import sibot as _sibot


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _priority(value):
    try:
        return int(float(value))
    except Exception:
        return 999


def alchemy_rpc_url(app, chain_id: int) -> str:
    """Lowest-priority enabled full Alchemy HTTP URL from rpc_endpoints.csv.

    Credentials remain only in the VPS-local CSV. ${...} placeholders are rejected,
    so this path never depends on ALCHEMY_API_KEY from the process environment.
    """
    path = Path(app.csv_dir) / "rpc_endpoints.csv"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        return ""
    candidates = []
    for row in rows:
        try:
            cid = int(float(str(row.get("chain_id") or "0").strip()))
        except Exception:
            continue
        if cid != int(chain_id) or not _bool(row.get("enabled"), True):
            continue
        url = str(row.get("url") or "").strip()
        low = url.lower()
        if not low.startswith(("https://", "http://")):
            continue
        if "$" in url or "alchemy.com" not in low:
            continue
        candidates.append((_priority(row.get("priority")), url))
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1] if candidates else ""


def history_provider(app, chain) -> str:
    return "ALCHEMY" if alchemy_rpc_url(app, int(chain.chain_id)) else "MISSING"


def _rpc(url: str, method: str, params, timeout=35):
    response = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=timeout,
        headers={"User-Agent": "BOOT-SiBot-Alchemy-History/1.0", "Content-Type": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Alchemy {method}: invalid JSON-RPC response")
    if payload.get("error"):
        err = payload.get("error") or {}
        raise RuntimeError(f"Alchemy {method}: {err.get('code')} {str(err.get('message') or '')[:260]}")
    return payload.get("result")


def _batch_rpc(url: str, method: str, params_rows: list[list], timeout=45) -> list:
    if not params_rows:
        return []
    payload = [
        {"jsonrpc": "2.0", "id": idx + 1, "method": method, "params": params}
        for idx, params in enumerate(params_rows)
    ]
    response = requests.post(
        url,
        json=payload,
        timeout=timeout,
        headers={"User-Agent": "BOOT-SiBot-Alchemy-History/1.0", "Content-Type": "application/json"},
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Alchemy {method}: batch response was not a list")
    by_id = {int(item.get("id") or 0): item for item in data if isinstance(item, dict)}
    out = []
    for idx in range(1, len(params_rows) + 1):
        item = by_id.get(idx) or {}
        if item.get("error"):
            err = item.get("error") or {}
            raise RuntimeError(f"Alchemy {method}: {err.get('code')} {str(err.get('message') or '')[:260]}")
        out.append(item.get("result"))
    return out


def _timestamp(row: dict) -> int:
    raw = str(((row.get("metadata") or {}).get("blockTimestamp")) or "").strip()
    if not raw:
        return 0
    try:
        return int(dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def _hex_int(value, default=0) -> int:
    try:
        text = str(value or "").strip()
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(Decimal(text))
    except Exception:
        return int(default)


def _raw_transfer_value(row: dict) -> int:
    raw = row.get("rawContract") or {}
    value = raw.get("value")
    if value not in (None, ""):
        return max(0, _hex_int(value, 0))
    amount = row.get("value")
    if amount in (None, ""):
        return 0
    decimals = max(0, min(36, _hex_int(raw.get("decimal"), 18)))
    try:
        return max(0, int(Decimal(str(amount)) * (Decimal(10) ** decimals)))
    except Exception:
        return 0


def _asset_pages(
    url: str,
    wallet: str,
    address_key: str,
    categories: list[str],
    cutoff_ts: int,
    max_pages: int,
    page_size: int,
    delay: float,
):
    rows = []
    page_key = ""
    reached = False
    for _ in range(max_pages):
        params = {
            "fromBlock": "0x0",
            "toBlock": "latest",
            address_key: wallet,
            "category": list(categories),
            "withMetadata": True,
            "excludeZeroValue": True,
            "maxCount": hex(max(1, min(1000, int(page_size)))),
            "order": "desc",
        }
        if page_key:
            params["pageKey"] = page_key
        result = _rpc(url, "alchemy_getAssetTransfers", [params]) or {}
        batch = list(result.get("transfers") or []) if isinstance(result, dict) else []
        rows.extend(batch)
        timestamps = [_timestamp(row) for row in batch]
        timestamps = [value for value in timestamps if value > 0]
        if timestamps and min(timestamps) <= cutoff_ts:
            reached = True
            break
        page_key = str(result.get("pageKey") or "") if isinstance(result, dict) else ""
        if not page_key:
            reached = True
            break
        time.sleep(max(0.0, delay))
    return rows, reached


def _dedupe(rows: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for row in rows:
        key = str(row.get("uniqueId") or "").strip()
        if not key:
            key = "|".join(str(row.get(k) or "") for k in ("hash", "category", "from", "to", "asset", "value"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _normalised_transfer_rows(transfers: list[dict]):
    token_rows = []
    internal_rows = []
    for row in transfers:
        category = str(row.get("category") or "").lower()
        tx_hash = str(row.get("hash") or "").lower()
        ts = _timestamp(row)
        raw = row.get("rawContract") or {}
        if category == "erc20":
            token = str(raw.get("address") or "").lower()
            if not Web3.is_address(token):
                continue
            decimals = max(0, min(36, _hex_int(raw.get("decimal"), 18)))
            token_rows.append({
                "hash": tx_hash,
                "contractAddress": token,
                "from": str(row.get("from") or "").lower(),
                "to": str(row.get("to") or "").lower(),
                "value": str(_raw_transfer_value(row)),
                "tokenSymbol": str(row.get("asset") or token[:10])[:32],
                "tokenDecimal": str(decimals),
                "timeStamp": str(ts),
            })
        elif category == "internal":
            internal_rows.append({
                "hash": tx_hash,
                "from": str(row.get("from") or "").lower(),
                "to": str(row.get("to") or "").lower(),
                "value": str(_raw_transfer_value(row)),
                "timeStamp": str(ts),
                "isError": "0",
            })
    return token_rows, internal_rows


def _trace_calls_to_wallet(node, wallet: str, tx_hash: str, ts: int, out: list[dict]):
    if isinstance(node, list):
        for item in node:
            _trace_calls_to_wallet(item, wallet, tx_hash, ts, out)
        return
    if not isinstance(node, dict):
        return
    target = str(node.get("to") or "").lower()
    value = _hex_int(node.get("value"), 0)
    if target == wallet.lower() and value > 0:
        out.append({
            "hash": tx_hash,
            "from": str(node.get("from") or "").lower(),
            "to": target,
            "value": str(value),
            "timeStamp": str(ts),
            "isError": "0",
        })
    for key in ("calls", "result", "trace"):
        child = node.get(key)
        if child is not None:
            _trace_calls_to_wallet(child, wallet, tx_hash, ts, out)


def _trace_internal(url: str, wallet: str, tx_hashes: list[str], ts_by_hash: dict[str, int]):
    rows = []
    for tx_hash in tx_hashes:
        result = _rpc(
            url,
            "debug_traceTransaction",
            [tx_hash, {"tracer": "callTracer", "tracerConfig": {"onlyTopCall": False}, "timeout": "20s"}],
            timeout=45,
        )
        _trace_calls_to_wallet(result, wallet, tx_hash, ts_by_hash.get(tx_hash, 0), rows)
    return rows


def _tx_context(url: str, transfers: list[dict], wallet: str):
    hashes = []
    ts_by_hash = {}
    block_by_hash = {}
    for row in transfers:
        tx_hash = str(row.get("hash") or "").lower()
        if not tx_hash:
            continue
        if tx_hash not in hashes:
            hashes.append(tx_hash)
        ts = _timestamp(row)
        if ts:
            ts_by_hash[tx_hash] = ts
        block = str(row.get("blockNum") or "").strip()
        if block:
            block_by_hash[tx_hash] = block

    tx_by_hash = {}
    receipt_by_hash = {}
    for start in range(0, len(hashes), 75):
        chunk = hashes[start:start + 75]
        txs = _batch_rpc(url, "eth_getTransactionByHash", [[h] for h in chunk])
        receipts = _batch_rpc(url, "eth_getTransactionReceipt", [[h] for h in chunk])
        for tx_hash, tx, receipt in zip(chunk, txs, receipts):
            if isinstance(tx, dict):
                tx_by_hash[tx_hash] = tx
            if isinstance(receipt, dict):
                receipt_by_hash[tx_hash] = receipt

    missing_blocks = sorted({block_by_hash[h] for h in hashes if h not in ts_by_hash and h in block_by_hash})
    block_ts = {}
    for start in range(0, len(missing_blocks), 75):
        chunk = missing_blocks[start:start + 75]
        blocks = _batch_rpc(url, "eth_getBlockByNumber", [[block, False] for block in chunk])
        for block, data in zip(chunk, blocks):
            if isinstance(data, dict):
                block_ts[block] = _hex_int(data.get("timestamp"), 0)

    normal = []
    outgoing_hashes = []
    w = wallet.lower()
    for tx_hash in hashes:
        tx = tx_by_hash.get(tx_hash) or {}
        if str(tx.get("from") or "").lower() != w:
            continue
        receipt = receipt_by_hash.get(tx_hash) or {}
        status = _hex_int(receipt.get("status"), 1)
        gas_price = receipt.get("effectiveGasPrice") or tx.get("gasPrice") or "0x0"
        ts = ts_by_hash.get(tx_hash) or block_ts.get(block_by_hash.get(tx_hash, ""), 0)
        normal.append({
            "hash": tx_hash,
            "from": str(tx.get("from") or ""),
            "to": str(tx.get("to") or ""),
            "value": str(_hex_int(tx.get("value"), 0)),
            "timeStamp": str(ts),
            "gasUsed": str(_hex_int(receipt.get("gasUsed"), 0)),
            "gasPrice": str(_hex_int(gas_price, 0)),
            "isError": "0" if status else "1",
            "txreceipt_status": "1" if status else "0",
        })
        outgoing_hashes.append(tx_hash)
    return normal, outgoing_hashes, ts_by_hash


def _store(app, chain, wallet, fetched_at, normal, token, internal, complete, error=""):
    trades, unmatched = _sibot.reconstruct_spot_trades(
        wallet,
        _sibot._routers(app, chain),
        normal,
        token,
        internal,
        chain.chain_id,
        chain.slug,
    )
    for row in trades:
        row["source"] = "ALCHEMY_TRANSFERS_DIRECT_NATIVE_FIFO"
    timestamps = [
        _sibot._int(row.get("timeStamp"), 0)
        for row in normal + token + internal
        if _sibot._int(row.get("timeStamp"), 0)
    ]
    coverage_start = min(timestamps) if timestamps else fetched_at
    coverage_end = max(timestamps) if timestamps else fetched_at
    complete = bool(complete and unmatched == 0 and not error)
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        conn.execute("DELETE FROM wallet_trades WHERE chain_id=? AND wallet=?", (chain.chain_id, wallet.lower()))
        for row in trades:
            conn.execute(
                """INSERT INTO wallet_trades(trade_id,chain_id,chain_slug,wallet,token,symbol,decimals,buy_tx,sell_tx,buy_ts,sell_ts,token_amount_raw,cost_native,proceeds_native,buy_gas_native,sell_gas_native,net_native,source,updated_at)
                   VALUES(:trade_id,:chain_id,:chain_slug,:wallet,:token,:symbol,:decimals,:buy_tx,:sell_tx,:buy_ts,:sell_ts,:token_amount_raw,:cost_native,:proceeds_native,:buy_gas_native,:sell_gas_native,:net_native,:source,:updated_at)""",
                row,
            )
        conn.execute(
            """INSERT INTO wallet_history_status(chain_id,chain_slug,wallet,fetched_at,coverage_start_ts,coverage_end_ts,history_complete,unmatched_sells,normal_rows,token_rows,internal_rows,error)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chain_id,wallet) DO UPDATE SET chain_slug=excluded.chain_slug,fetched_at=excluded.fetched_at,coverage_start_ts=excluded.coverage_start_ts,coverage_end_ts=excluded.coverage_end_ts,history_complete=excluded.history_complete,unmatched_sells=excluded.unmatched_sells,normal_rows=excluded.normal_rows,token_rows=excluded.token_rows,internal_rows=excluded.internal_rows,error=excluded.error""",
            (
                chain.chain_id,
                chain.slug,
                wallet.lower(),
                fetched_at,
                coverage_start,
                coverage_end,
                1 if complete else 0,
                unmatched,
                len(normal),
                len(token),
                len(internal),
                error[:500],
            ),
        )
        conn.commit()
    return {
        "wallet": wallet,
        "trades": len(trades),
        "complete": complete,
        "unmatched_sells": unmatched,
        "provider": "ALCHEMY",
        **({"error": error} if error else {}),
    }


def refresh_wallet_history(app, chain, wallet: str) -> dict:
    """Reconstruct EVM history using only Alchemy configured in rpc_endpoints.csv."""
    url = alchemy_rpc_url(app, int(chain.chain_id))
    fetched_at = int(time.time())
    if not url:
        error = "Alchemy history endpoint missing from rpc_endpoints.csv"
        return _store(app, chain, wallet, fetched_at, [], [], [], False, error)

    cfg = _sibot.platform_settings(app, chain.chain_id)
    fetch_days = max(30, min(3650, _sibot._int(cfg.get("history_fetch_days"), 365)))
    cutoff = int(time.time()) - fetch_days * 86400
    max_pages = max(1, min(40, _sibot._int(cfg.get("history_max_pages"), 3)))
    # Alchemy Transfers API allows at most 1,000 results per page.
    page_size = max(100, min(1000, _sibot._int(cfg.get("history_page_size"), 1000)))
    delay = max(0.0, min(2.0, _sibot._float(cfg.get("history_api_delay_seconds"), 0.15)))

    try:
        outbound, c_out = _asset_pages(
            url, wallet, "fromAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay
        )
        time.sleep(delay)
        inbound, c_in = _asset_pages(
            url, wallet, "toAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay
        )
        transfers = _dedupe(outbound + inbound)
        normal, outgoing_hashes, ts_by_hash = _tx_context(url, transfers, wallet)
        token, _ = _normalised_transfer_rows(transfers)

        internal = []
        c_internal = False
        try:
            internal_transfers, c_internal = _asset_pages(
                url, wallet, "toAddress", ["internal"], cutoff, max_pages, page_size, delay
            )
            _, internal = _normalised_transfer_rows(_dedupe(internal_transfers))
        except Exception:
            # Some Alchemy networks expose historical transfers but not the
            # `internal` category. Use Alchemy's own debug trace RPC instead;
            # if that is unavailable too, fail closed rather than inventing P&L.
            internal = _trace_internal(url, wallet, outgoing_hashes, ts_by_hash)
            c_internal = True

        return _store(
            app,
            chain,
            wallet,
            fetched_at,
            normal,
            token,
            internal,
            bool(c_out and c_in and c_internal),
        )
    except Exception as exc:
        error = f"AlchemyHistoryError: {type(exc).__name__}: {str(exc)[:420]}"
        return _store(app, chain, wallet, fetched_at, [], [], [], False, error)


def install():
    if getattr(_sibot, "_alchemy_history_patch_installed", False):
        return
    _sibot.refresh_wallet_history = refresh_wallet_history
    _sibot.history_provider = history_provider
    _sibot.alchemy_history_rpc_url = alchemy_rpc_url
    _sibot._alchemy_history_patch_installed = True


install()
