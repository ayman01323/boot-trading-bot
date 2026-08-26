from __future__ import annotations

"""Bounded RPC failover for full-power *read-only* market discovery.

The direct V2/V3 scanner historically constructed LiveTrader(require_wallet=False),
but LiveTrader always bound Web3 to chain.rpc_urls[0]. A throttled first endpoint
therefore starved GPT/Base discovery even when other configured RPCs were healthy.

This patch is deliberately scoped to learnerbot.full_power_scanner only:
- real LIVE/manual/protected execution continues to use live_executor.LiveTrader;
- scanner instances never load a signing key;
- at most three configured HTTP RPC endpoints are tried;
- only transient provider/transport failures trigger quote failover;
- deterministic route/contract reverts are not retried across providers;
- endpoint URLs are never written to rejection text or logs;
- route-check budgets, quote freshness, profit floors and all LIVE gates are unchanged.
"""

import threading
from typing import Any

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from . import full_power_scanner as _fp
from . import live_executor as _live

_BASE_TRADER = _fp.LiveTrader
_ORIGINAL_V3_QUOTE = _fp._v3_quote
_CURSOR_LOCK = threading.Lock()
_RPC_CURSOR: dict[int, int] = {}
_MAX_ENDPOINTS = 3


class _ScannerEndpointUnavailable(RuntimeError):
    pass


def _exception_chain(exc: BaseException):
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _rpc_error_kind(exc: BaseException) -> str:
    """Classify only failures that are safe to retry on another configured RPC."""
    for item in _exception_chain(exc):
        response = getattr(item, "response", None)
        status = getattr(response, "status_code", None)
        if status == 429:
            return "provider_rate_limit"
        if status in {502, 503, 504}:
            return "provider_transport"
        text = str(item or "").lower()
        if any(marker in text for marker in (
            "http 429",
            "status code 429",
            "too many requests",
            "rate limit",
            "compute units per second",
            "cu per second",
            "quota exceeded",
        )):
            return "provider_rate_limit"
        if any(marker in text for marker in (
            "timed out",
            "timeout",
            "connection reset",
            "connection aborted",
            "connection refused",
            "connection closed",
            "max retries exceeded",
            "temporary failure",
            "temporarily unavailable",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "http 502",
            "http 503",
            "http 504",
        )):
            return "provider_transport"
    return ""


def _http_rpc_urls(chain) -> list[str]:
    out: list[str] = []
    for raw in list(getattr(chain, "rpc_urls", []) or []):
        url = str(raw or "").strip()
        if not url.lower().startswith(("https://", "http://")):
            continue
        if url not in out:
            out.append(url)
    return out


def _rotated_indices(chain_id: int, count: int) -> list[int]:
    if count <= 0:
        return []
    with _CURSOR_LOCK:
        start = int(_RPC_CURSOR.get(int(chain_id), 0)) % count
        _RPC_CURSOR[int(chain_id)] = (start + 1) % count
    return [(start + offset) % count for offset in range(min(_MAX_ENDPOINTS, count))]


class ScannerFailoverLiveTrader(_BASE_TRADER):
    """Scanner-local LiveTrader variant with no signer and bounded RPC failover."""

    def __init__(
        self,
        app,
        chain_slug: str,
        *,
        telegram_id=None,
        wallet_id=None,
        private_key=None,
        require_wallet=True,
        router_override=None,
    ):
        # Never alter real execution semantics. Any caller that wants/identifies a
        # wallet receives the original LiveTrader constructor unchanged.
        if require_wallet or telegram_id is not None or wallet_id is not None or private_key is not None:
            super().__init__(
                app,
                chain_slug,
                telegram_id=telegram_id,
                wallet_id=wallet_id,
                private_key=private_key,
                require_wallet=require_wallet,
                router_override=router_override,
            )
            self._scanner_read_only = False
            return

        self.app = app
        self.telegram_id = None
        self.wallet_id = None
        self.chain = next(
            (c for c in _live.load_chains(app, enabled_only=False) if c.slug == str(chain_slug).lower()),
            None,
        )
        if not self.chain:
            raise _live.LiveTradingError(f"Unknown chain: {chain_slug}")
        if not self.chain.enabled:
            raise _live.LiveTradingError(f"Chain is disabled in CSVbot/chains.csv: {self.chain.name}")
        if self.chain.chain_id not in _live.V2_ROUTERS:
            raise _live.LiveTradingError(f"Live V2 execution is not configured for {self.chain.name}")

        self._scanner_rpc_urls = _http_rpc_urls(self.chain)
        if not self._scanner_rpc_urls:
            raise _live.LiveTradingError(f"No enabled HTTP RPC endpoint for {self.chain.name}")

        self.settings = _live.load_kv_scoped(app.csv_dir / "live_trading_settings.csv", self.chain.chain_id)
        configured_router = str(self.settings.get("router_address") or "").strip()
        if router_override:
            override = Web3.to_checksum_address(router_override)
            allowed = {
                str(r.get("router") or "").strip().lower()
                for r in _live.load_dex_registry(app.csv_dir, self.chain.chain_id)
                if str(r.get("version") or "").strip().upper() == "V2"
            }
            if override.lower() not in allowed:
                raise _live.LiveTradingError("Router override is not an enabled V2 venue in CSVbot/dex_registry.csv")
            self.router_address = override
        else:
            self.router_address = Web3.to_checksum_address(
                configured_router or _live.V2_ROUTERS[self.chain.chain_id]
            )

        self.account = None
        self.address = None
        self.wrapped = Web3.to_checksum_address(self.chain.wrapped_base_address)
        self._scanner_read_only = True
        self._scanner_rpc_index = -1

        last_kind = "provider_transport"
        for idx in _rotated_indices(self.chain.chain_id, len(self._scanner_rpc_urls)):
            try:
                self._bind_scanner_rpc(idx)
                return
            except _ScannerEndpointUnavailable as exc:
                last_kind = str(exc) or last_kind
                continue
            except Exception as exc:
                kind = _rpc_error_kind(exc)
                if kind:
                    last_kind = kind
                    continue
                raise
        raise _live.LiveTradingError(
            f"Read-only scanner RPC unavailable after bounded failover ({last_kind})"
        )

    def _bind_scanner_rpc(self, index: int) -> None:
        index = int(index)
        url = self._scanner_rpc_urls[index]
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))
            if self.chain.chain_id in {56, 137}:
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if not w3.is_connected():
                raise _ScannerEndpointUnavailable("provider_transport")
            if int(w3.eth.chain_id) != int(self.chain.chain_id):
                raise _ScannerEndpointUnavailable("provider_chain_mismatch")
            code = w3.eth.get_code(self.router_address)
            if not code:
                raise _ScannerEndpointUnavailable("provider_router_unavailable")
        except _ScannerEndpointUnavailable:
            raise
        except Exception as exc:
            kind = _rpc_error_kind(exc)
            if kind:
                raise _ScannerEndpointUnavailable(kind) from None
            raise

        self.w3 = w3
        self.router = self.w3.eth.contract(address=self.router_address, abi=_live.V2_ROUTER_ABI)
        self._scanner_rpc_index = index

    def _scanner_failover(self, attempted: set[int] | None = None) -> bool:
        if not getattr(self, "_scanner_read_only", False):
            return False
        attempted = attempted if attempted is not None else set()
        current = int(getattr(self, "_scanner_rpc_index", -1))
        if current >= 0:
            attempted.add(current)
        count = len(self._scanner_rpc_urls)
        for offset in range(1, count + 1):
            idx = (max(current, 0) + offset) % count
            if idx in attempted:
                continue
            attempted.add(idx)
            if len(attempted) > _MAX_ENDPOINTS:
                return False
            try:
                self._bind_scanner_rpc(idx)
                return True
            except _ScannerEndpointUnavailable:
                continue
            except Exception as exc:
                if _rpc_error_kind(exc):
                    continue
                raise
        return False

    def cycle_quote(self, path: list[str], amount_native) -> dict:
        attempted = {int(getattr(self, "_scanner_rpc_index", -1))}
        last_kind = ""
        while True:
            try:
                return super().cycle_quote(path, amount_native)
            except Exception as exc:
                kind = _rpc_error_kind(exc)
                if not kind:
                    raise
                last_kind = kind
                if len({x for x in attempted if x >= 0}) >= min(_MAX_ENDPOINTS, len(self._scanner_rpc_urls)):
                    break
                if not self._scanner_failover(attempted):
                    break
        raise _live.LiveTradingError(
            f"Exact V2 route quote failed after bounded read-only RPC failover ({last_kind or 'provider_transport'})"
        ) from None


def v3_quote_with_rpc_failover(trader, quoter_addr: str, path: list[str], fees: list[int], amount):
    if not isinstance(trader, ScannerFailoverLiveTrader) or not getattr(trader, "_scanner_read_only", False):
        return _ORIGINAL_V3_QUOTE(trader, quoter_addr, path, fees, amount)

    attempted = {int(getattr(trader, "_scanner_rpc_index", -1))}
    last_kind = ""
    while True:
        try:
            return _ORIGINAL_V3_QUOTE(trader, quoter_addr, path, fees, amount)
        except Exception as exc:
            kind = _rpc_error_kind(exc)
            if not kind:
                raise
            last_kind = kind
            if len({x for x in attempted if x >= 0}) >= min(_MAX_ENDPOINTS, len(trader._scanner_rpc_urls)):
                break
            if not trader._scanner_failover(attempted):
                break
    raise RuntimeError(
        f"V3 quote failed after bounded read-only RPC failover ({last_kind or 'provider_transport'})"
    ) from None


def install() -> None:
    if getattr(_fp, "_scanner_rpc_failover_installed", False):
        return
    _fp.LiveTrader = ScannerFailoverLiveTrader
    _fp._v3_quote = v3_quote_with_rpc_failover
    _fp._scanner_rpc_failover_installed = True
    print(
        "[full-power-scanner-rpc-failover] installed=true scope=read-only "
        "max_endpoints=3 signer_unchanged=true execution_provider_unchanged=true "
        "quote_budget_unchanged=true freshness_unchanged=true safety_unchanged=true",
        flush=True,
    )


install()
