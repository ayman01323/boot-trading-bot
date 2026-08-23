from __future__ import annotations

import threading
import time
from dataclasses import replace

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from . import live_executor as _live

_ORIGINAL_LOAD_CHAINS = _live.load_chains
_CACHE_LOCK = threading.RLock()
_CACHE: dict[tuple[int, tuple[str, ...]], tuple[float, tuple[str, ...]]] = {}
_CACHE_SECONDS = 30.0


def _valid_http_url(url: str) -> bool:
    text = str(url or "").strip()
    return text.startswith(("https://", "http://")) and "$" not in text


def _probe(url: str, chain_id: int) -> bool:
    """Return True only when the endpoint is reachable and reports the expected chain."""
    if not _valid_http_url(url):
        return False
    try:
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
        if int(chain_id) in {56, 137}:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if not w3.is_connected():
            return False
        return int(w3.eth.chain_id) == int(chain_id)
    except Exception:
        # Deliberately suppress provider details: private RPC URLs may contain API keys.
        return False


def _ordered_rpc_urls(chain) -> list[str]:
    urls: list[str] = []
    for raw in list(getattr(chain, "rpc_urls", []) or []):
        url = str(raw or "").strip()
        if url and url not in urls:
            urls.append(url)
    if len(urls) <= 1:
        return urls

    key = (int(chain.chain_id), tuple(urls))
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] > now:
            return list(cached[1])

    ordered = list(urls)
    for idx, url in enumerate(urls):
        if _probe(url, int(chain.chain_id)):
            # Preserve configured priority whenever the first endpoint is healthy.
            # If a higher-priority endpoint is down, move only the first verified
            # fallback to the front; retain all remaining endpoints for later checks.
            ordered = [url] + urls[:idx] + urls[idx + 1 :]
            break

    with _CACHE_LOCK:
        _CACHE[key] = (now + _CACHE_SECONDS, tuple(ordered))
    return ordered


def _load_chains_with_execution_failover(app, enabled_only=False):
    chains = _ORIGINAL_LOAD_CHAINS(app, enabled_only=enabled_only)
    out = []
    for chain in chains:
        urls = _ordered_rpc_urls(chain)
        if urls != list(chain.rpc_urls):
            chain = replace(chain, rpc_urls=urls)
        out.append(chain)
    return out


def install() -> None:
    # LiveTrader resolves this module-global name at construction time. Replacing
    # only that resolver keeps wallet/signing/LIVE/risk checks unchanged while
    # making the RPC choice resilient to a stale first endpoint.
    if getattr(_live, "_execution_rpc_failover_installed", False):
        return
    _live.load_chains = _load_chains_with_execution_failover
    _live._execution_rpc_failover_installed = True


install()
