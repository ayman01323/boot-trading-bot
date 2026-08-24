from __future__ import annotations

import os
import re
from typing import Any

import requests

from . import solana_sibot as _sol


_TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}
_TRANSIENT_RPC_TEXT = (
    "429",
    "rate limit",
    "too many requests",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "service unavailable",
    "connection reset",
    "connection aborted",
    "gateway timeout",
    "node is behind",
    "node is unhealthy",
)


class SolanaRpcEndpointError(RuntimeError):
    """Sanitised endpoint failure that never embeds an RPC URL/API key."""

    def __init__(self, method: str, detail: str, *, transient: bool, status_code: int = 0):
        self.method = str(method)
        self.transient = bool(transient)
        self.status_code = int(status_code or 0)
        super().__init__(f"Solana RPC {self.method}: {str(detail)[:240]}")


def _split_urls(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;,\s]+", str(value or "")) if x.strip()]


def _candidate_urls(app) -> list[str]:
    """Return unique RPC endpoints in production-safe priority order.

    Explicit multi-endpoint configuration wins. A dedicated Helius endpoint is
    preferred over Solana's public endpoint when a Helius key is already present.
    The public mainnet endpoint remains a final availability fallback only.
    """
    urls: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in urls:
            urls.append(value)

    for value in _split_urls(os.getenv("SOLANA_RPC_URLS", "")):
        add(value)
    for value in _split_urls(os.getenv("SOLANA_RPC_FALLBACK_URLS", "")):
        add(value)

    explicit = os.getenv("SOLANA_RPC_URL", "").strip()
    if explicit and explicit != _sol.DEFAULT_RPC:
        add(explicit)

    add(os.getenv("HELIUS_RPC_URL", "").strip())
    helius_key = os.getenv("HELIUS_API_KEY", "").strip()
    if helius_key:
        add(f"https://mainnet.helius-rpc.com/?api-key={helius_key}")

    try:
        configured = str(_sol.settings(app).get("rpc_url") or "").strip()
    except Exception:
        configured = ""
    if configured and configured != _sol.DEFAULT_RPC:
        add(configured)

    add(_sol.DEFAULT_RPC)
    return urls


def _rpc_error_is_transient(error: Any) -> bool:
    text = str(error or "").lower()
    return any(marker in text for marker in _TRANSIENT_RPC_TEXT)


def _post_one(url: str, method: str, params: list) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=35,
            headers={"User-Agent": "BOOT-SiBot-Solana/1.0"},
        )
    except requests.RequestException as exc:
        raise SolanaRpcEndpointError(
            method,
            f"transport {type(exc).__name__}",
            transient=True,
        ) from None

    status = int(getattr(response, "status_code", 0) or 0)
    if not 200 <= status < 300:
        raise SolanaRpcEndpointError(
            method,
            f"HTTP {status}",
            transient=status in _TRANSIENT_HTTP,
            status_code=status,
        )

    try:
        data = response.json()
    except Exception as exc:
        raise SolanaRpcEndpointError(
            method,
            f"invalid JSON response ({type(exc).__name__})",
            transient=True,
            status_code=status,
        ) from None

    # A 2xx response is not automatically a successful JSON-RPC response. Treat a
    # malformed provider/proxy envelope as an endpoint fault so another configured
    # provider gets a chance instead of silently returning None to the caller.
    if not isinstance(data, dict):
        raise SolanaRpcEndpointError(
            method,
            "invalid JSON-RPC envelope",
            transient=True,
            status_code=status,
        )

    error = data.get("error")
    if error:
        raise SolanaRpcEndpointError(
            method,
            f"JSON-RPC error {str(error)[:180]}",
            transient=_rpc_error_is_transient(error),
            status_code=status,
        )

    if "result" not in data:
        raise SolanaRpcEndpointError(
            method,
            "JSON-RPC response missing result",
            transient=True,
            status_code=status,
        )
    return data["result"]


def rpc_failover(app, method: str, params: list):
    urls = _candidate_urls(app)
    if not urls:
        raise SolanaRpcEndpointError(method, "no RPC endpoint configured", transient=False)

    last: SolanaRpcEndpointError | None = None
    for url in urls:
        try:
            return _post_one(url, method, params)
        except SolanaRpcEndpointError as exc:
            last = exc
            if not exc.transient:
                raise
            continue

    if last is not None:
        raise last
    raise SolanaRpcEndpointError(method, "all RPC endpoints failed", transient=True)


def install() -> None:
    _sol._rpc = rpc_failover
    print(
        "[solana-rpc-failover] multi_endpoint=true helius_fallback=true "
        "public_rpc_last=true secret_safe_errors=true"
    )


install()
