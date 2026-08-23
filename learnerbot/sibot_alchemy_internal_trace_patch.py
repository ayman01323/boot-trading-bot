from __future__ import annotations

import time
from collections import defaultdict
from contextlib import closing

from . import sibot as _sibot
from . import sibot_alchemy_history_patch as _alchemy

# Capture the Alchemy provider's queue function before this final migration layer
# replaces it. We fall back to it once legacy Etherscan-error candidates are gone.
_PREV_NEXT_HISTORY_WALLET = _sibot._next_history_wallet

# Alchemy's Transfers endpoint currently exposes internal-transfer history only
# on Ethereum mainnet, Polygon mainnet and Base mainnet. Arbitrum and BNB may
# return a valid empty response for category=internal, so an exception-only
# fallback silently loses sell proceeds on those chains.
_INTERNAL_TRANSFERS_API_CHAINS = {1, 137, 8453}
_CONTEXT_BATCH_SIZE = 10
_CONTEXT_BATCH_PAUSE_SECONDS = 0.35


def _direct_flow_hashes(transfers, wallet: str) -> list[str]:
    """Return ERC-20 tx hashes that can satisfy the existing direct-trade model.

    The downstream FIFO reconstructor accepts only one net ERC-20 direction per
    transaction. Filtering before eth_getTransactionByHash/Receipt avoids spending
    provider compute units on unrelated transfers that can never become a SiBot
    native<->ERC20 trade. This narrows provider work without widening trade scope.
    """
    w = str(wallet or "").lower()
    flows = defaultdict(lambda: defaultdict(int))
    order = []
    seen = set()
    for row in transfers:
        if str(row.get("category") or "").lower() != "erc20":
            continue
        h = str(row.get("hash") or "").lower()
        token = str((row.get("rawContract") or {}).get("address") or "").lower()
        if not h or not token:
            continue
        value = int(_alchemy._raw_transfer_value(row) or 0)
        if value <= 0:
            continue
        frm = str(row.get("from") or "").lower()
        to = str(row.get("to") or "").lower()
        delta = 0
        if to == w and frm != w:
            delta += value
        if frm == w and to != w:
            delta -= value
        if not delta:
            continue
        flows[h][token] += delta
        if h not in seen:
            seen.add(h)
            order.append(h)

    out = []
    for h in order:
        nonzero = [value for value in flows[h].values() if value]
        if len(nonzero) == 1:
            out.append(h)
    return out


def _direct_context_transfers(transfers, wallet: str) -> list[dict]:
    hashes = set(_direct_flow_hashes(transfers, wallet))
    if not hashes:
        return []
    return [row for row in transfers if str(row.get("hash") or "").lower() in hashes]


def _direct_tx_context(url: str, transfers: list[dict], wallet: str, routers: set[str]):
    """Fetch tx/receipt context only for hashes the current gate can reconstruct.

    Transaction lookups happen first. Receipts are fetched only for successful
    candidate shapes initiated by the wallet and addressed to a configured DEX
    router. The configured-router restriction is deliberately preserved here and
    again in reconstruct_spot_trades; this optimisation must not broaden which
    historical trades can qualify a leader.
    """
    hashes = []
    ts_by_hash = {}
    block_by_hash = {}
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

    tx_by_hash = {}
    for start in range(0, len(hashes), _CONTEXT_BATCH_SIZE):
        chunk = hashes[start:start + _CONTEXT_BATCH_SIZE]
        txs = _alchemy._batch_rpc(url, "eth_getTransactionByHash", [[h] for h in chunk])
        for tx_hash, tx in zip(chunk, txs):
            if isinstance(tx, dict):
                tx_by_hash[tx_hash] = tx
        if start + _CONTEXT_BATCH_SIZE < len(hashes):
            time.sleep(_CONTEXT_BATCH_PAUSE_SECONDS)

    w = str(wallet or "").lower()
    allowed = {str(router or "").lower() for router in routers if str(router or "").strip()}
    eligible = []
    for tx_hash in hashes:
        tx = tx_by_hash.get(tx_hash) or {}
        if str(tx.get("from") or "").lower() != w:
            continue
        to = str(tx.get("to") or "").lower()
        if allowed and to not in allowed:
            continue
        eligible.append(tx_hash)

    receipt_by_hash = {}
    if eligible:
        # Preserve the existing provider pacing between transaction and receipt work.
        time.sleep(_CONTEXT_BATCH_PAUSE_SECONDS)
    for start in range(0, len(eligible), _CONTEXT_BATCH_SIZE):
        chunk = eligible[start:start + _CONTEXT_BATCH_SIZE]
        receipts = _alchemy._batch_rpc(url, "eth_getTransactionReceipt", [[h] for h in chunk])
        for tx_hash, receipt in zip(chunk, receipts):
            if isinstance(receipt, dict):
                receipt_by_hash[tx_hash] = receipt
        if start + _CONTEXT_BATCH_SIZE < len(eligible):
            time.sleep(_CONTEXT_BATCH_PAUSE_SECONDS)

    missing_blocks = sorted({
        block_by_hash[h]
        for h in eligible
        if h not in ts_by_hash and h in block_by_hash
    })
    block_ts = {}
    for start in range(0, len(missing_blocks), _CONTEXT_BATCH_SIZE):
        chunk = missing_blocks[start:start + _CONTEXT_BATCH_SIZE]
        blocks = _alchemy._batch_rpc(url, "eth_getBlockByNumber", [[block, False] for block in chunk])
        for block, data in zip(chunk, blocks):
            if isinstance(data, dict):
                block_ts[block] = _alchemy._hex_int(data.get("timestamp"), 0)
        if start + _CONTEXT_BATCH_SIZE < len(missing_blocks):
            time.sleep(_CONTEXT_BATCH_PAUSE_SECONDS)

    normal = []
    outgoing_hashes = []
    for tx_hash in eligible:
        tx = tx_by_hash.get(tx_hash) or {}
        receipt = receipt_by_hash.get(tx_hash)
        if not isinstance(receipt, dict):
            # Missing receipt evidence is not treated as a successful transaction.
            continue
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


def _trace_candidate_hashes(normal_rows, token_rows, wallet: str, routers: set[str]) -> list[str]:
    """Return only direct router tx hashes that have an ERC-20 wallet flow."""
    w = str(wallet or "").lower()
    token_hashes = set()
    for row in token_rows:
        h = str(row.get("hash") or "").lower()
        if not h:
            continue
        frm = str(row.get("from") or "").lower()
        to = str(row.get("to") or "").lower()
        if frm == w or to == w:
            token_hashes.add(h)

    out = []
    seen = set()
    for tx in normal_rows:
        h = str(tx.get("hash") or "").lower()
        if not h or h in seen or h not in token_hashes:
            continue
        if not _sibot._successful_normal(tx):
            continue
        if str(tx.get("from") or "").lower() != w:
            continue
        to = str(tx.get("to") or "").lower()
        if routers and to not in routers:
            continue
        seen.add(h)
        out.append(h)
    return out


def _first_legacy_candidate(candidates, legacy_wallets) -> str | None:
    legacy = {str(wallet or "").lower() for wallet in legacy_wallets if str(wallet or "").strip()}
    for wallet in candidates:
        candidate = str(wallet or "").lower()
        if candidate and candidate in legacy:
            return candidate
    return None


def _next_history_wallet(app, chain):
    """Migrate current high-priority candidates before old low-value rows."""
    if not _alchemy.alchemy_rpc_url(app, int(chain.chain_id)):
        return _PREV_NEXT_HISTORY_WALLET(app, chain)

    cfg = _sibot.platform_settings(app, chain.chain_id)
    limit = max(20, min(500, _sibot._int(cfg.get("history_candidate_wallets"), 40)))
    candidates = [str(wallet or "").lower() for wallet in _sibot._candidate_wallets(app, chain, limit)]
    if candidates:
        with closing(_sibot.connect(app)) as conn:
            rows = conn.execute(
                """SELECT wallet FROM wallet_history_status
                   WHERE chain_id=? AND error LIKE '%ETHERSCAN_API_KEY%'""",
                (chain.chain_id,),
            ).fetchall()
        chosen = _first_legacy_candidate(candidates, [row["wallet"] for row in rows])
        if chosen:
            return chosen

    return _PREV_NEXT_HISTORY_WALLET(app, chain)


def refresh_wallet_history(app, chain, wallet: str) -> dict:
    """Alchemy-only EVM history with bounded direct-trade context reconstruction."""
    url = _alchemy.alchemy_rpc_url(app, int(chain.chain_id))
    fetched_at = int(time.time())
    if not url:
        return _alchemy._store_error(
            app,
            chain,
            wallet,
            fetched_at,
            "Alchemy history endpoint missing from rpc_endpoints.csv",
        )

    cfg = _sibot.platform_settings(app, chain.chain_id)
    fetch_days = max(30, min(3650, _sibot._int(cfg.get("history_fetch_days"), 365)))
    cutoff = int(time.time()) - fetch_days * 86400
    max_pages = max(1, min(40, _sibot._int(cfg.get("history_max_pages"), 3)))
    page_size = max(100, min(1000, _sibot._int(cfg.get("history_page_size"), 1000)))
    delay = max(0.0, min(2.0, _sibot._float(cfg.get("history_api_delay_seconds"), 0.15)))

    try:
        outbound, c_out = _alchemy._asset_pages(
            url, wallet, "fromAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay
        )
        time.sleep(delay)
        inbound, c_in = _alchemy._asset_pages(
            url, wallet, "toAddress", ["external", "erc20"], cutoff, max_pages, page_size, delay
        )
        transfers = _alchemy._dedupe(outbound + inbound)
        routers = _sibot._routers(app, chain)
        context_transfers = _direct_context_transfers(transfers, wallet)
        normal, _outgoing_hashes, ts_by_hash = _direct_tx_context(
            url, context_transfers, wallet, routers
        )
        token, _ = _alchemy._normalised_transfer_rows(context_transfers)
        trace_hashes = _trace_candidate_hashes(normal, token, wallet, routers)
        normal_hashes = {str(row.get("hash") or "").lower() for row in normal}

        if int(chain.chain_id) in _INTERNAL_TRANSFERS_API_CHAINS:
            try:
                internal_transfers, c_internal = _alchemy._asset_pages(
                    url, wallet, "toAddress", ["internal"], cutoff, max_pages, page_size, delay
                )
                _, internal = _alchemy._normalised_transfer_rows(_alchemy._dedupe(internal_transfers))
                internal = [
                    row for row in internal
                    if str(row.get("hash") or "").lower() in normal_hashes
                ]
            except Exception:
                # Supported-chain API failure: use trace as an accuracy-preserving
                # fallback. Any trace failure still fails the whole wallet closed.
                internal = _alchemy._trace_internal(url, wallet, trace_hashes, ts_by_hash)
                c_internal = True
        else:
            # Arbitrum and BNB do not have reliable internal-history results from
            # alchemy_getAssetTransfers. Trace only transactions that survived the
            # same configured-router/direct-token-flow gate used by reconstruction.
            internal = _alchemy._trace_internal(url, wallet, trace_hashes, ts_by_hash)
            c_internal = True

        return _alchemy._store_success(
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
        return _alchemy._store_error(app, chain, wallet, fetched_at, error)


def install() -> None:
    if getattr(_sibot, "_alchemy_internal_trace_patch_installed", False):
        return
    _sibot.refresh_wallet_history = refresh_wallet_history
    _sibot._next_history_wallet = _next_history_wallet
    _sibot._alchemy_internal_trace_patch_installed = True


install()
