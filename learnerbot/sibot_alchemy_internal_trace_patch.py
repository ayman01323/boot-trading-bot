from __future__ import annotations

import time
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


def _trace_candidate_hashes(normal_rows, token_rows, wallet: str, routers: set[str]) -> list[str]:
    """Return only direct router tx hashes that have an ERC-20 wallet flow.

    This bounds debug tracing to transactions that can actually become a direct
    native<->ERC20 SiBot trade. It covers SELL proceeds and BUY refunds without
    tracing unrelated wallet activity.
    """
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
    """Migrate current high-priority candidates before old low-value rows.

    The original queue sorts by oldest fetched_at. During an explorer-provider
    migration that can spend many cycles refreshing stale low-priority wallets
    while the current Top-20 candidates still carry obsolete Etherscan errors.
    Preserve candidate ranking order for those legacy errors, then fall back to
    the normal age-based Alchemy queue once migration is complete.
    """
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
    """Alchemy-only EVM history with chain-aware internal native reconstruction."""
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
        normal, _outgoing_hashes, ts_by_hash = _alchemy._tx_context(url, transfers, wallet)
        token, _ = _alchemy._normalised_transfer_rows(transfers)
        routers = _sibot._routers(app, chain)
        trace_hashes = _trace_candidate_hashes(normal, token, wallet, routers)

        if int(chain.chain_id) in _INTERNAL_TRANSFERS_API_CHAINS:
            try:
                internal_transfers, c_internal = _alchemy._asset_pages(
                    url, wallet, "toAddress", ["internal"], cutoff, max_pages, page_size, delay
                )
                _, internal = _alchemy._normalised_transfer_rows(_alchemy._dedupe(internal_transfers))
            except Exception:
                # Supported-chain API failure: use trace as an accuracy-preserving
                # fallback. Any trace failure still fails the whole wallet closed.
                internal = _alchemy._trace_internal(url, wallet, trace_hashes, ts_by_hash)
                c_internal = True
        else:
            # Arbitrum and BNB do not have reliable internal-history results from
            # alchemy_getAssetTransfers. Trace the swap-relevant transactions even
            # when the internal Transfers call would have returned an empty success.
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
