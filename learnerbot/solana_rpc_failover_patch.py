from __future__ import annotations

import os
import re
import threading
import time
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

# Process-local endpoint health. RPC URLs may embed API keys, so these values are
# intentionally never printed, persisted, or included in exceptions.
_ENDPOINT_LOCK = threading.Lock()
_ENDPOINT_STATE: dict[str, dict[str, float | int]] = {}


def _float_env(name: str, default: float, *, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except Exception:
        value = float(default)
    return max(low, min(high, value))


def _int_env(name: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(float(os.getenv(name, str(default)).strip()))
    except Exception:
        value = int(default)
    return max(low, min(high, value))


def _rate_limit_base_cooldown() -> float:
    return _float_env("SOLANA_RPC_429_COOLDOWN_SECONDS", 30.0, low=1.0, high=300.0)


def _rate_limit_max_cooldown() -> float:
    return _float_env("SOLANA_RPC_429_MAX_COOLDOWN_SECONDS", 180.0, low=5.0, high=900.0)


def _transient_cooldown() -> float:
    return _float_env("SOLANA_RPC_TRANSIENT_COOLDOWN_SECONDS", 3.0, low=0.25, high=30.0)


def _max_inflight_per_endpoint() -> int:
    return _int_env("SOLANA_RPC_MAX_INFLIGHT_PER_ENDPOINT", 2, low=1, high=16)


class SolanaRpcEndpointError(RuntimeError):
    """Sanitised endpoint failure that never embeds an RPC URL/API key."""

    def __init__(
        self,
        method: str,
        detail: str,
        *,
        transient: bool,
        status_code: int = 0,
        retry_after_seconds: float = 0.0,
    ):
        self.method = str(method)
        self.transient = bool(transient)
        self.status_code = int(status_code or 0)
        self.retry_after_seconds = max(0.0, float(retry_after_seconds or 0.0))
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


def _retry_after(response: Any) -> float:
    try:
        raw = str((getattr(response, "headers", {}) or {}).get("Retry-After") or "").strip()
        if raw:
            return max(0.0, min(900.0, float(raw)))
    except Exception:
        pass
    return 0.0


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
            retry_after_seconds=_retry_after(response),
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


def _state_for(url: str) -> dict[str, float | int]:
    state = _ENDPOINT_STATE.get(url)
    if state is None:
        state = {
            "cooldown_until": 0.0,
            "rate_limit_strikes": 0,
            "inflight": 0,
            "last_started": 0.0,
        }
        _ENDPOINT_STATE[url] = state
    return state


def _is_rate_limited(exc: SolanaRpcEndpointError) -> bool:
    if int(exc.status_code or 0) == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _acquire_endpoint(urls: list[str], tried: set[str]) -> tuple[str | None, str]:
    """Reserve one healthy endpoint without exposing its URL in status text.

    Prefer explicitly configured providers. Solana's public endpoint remains an
    overflow/failover endpoint and is not used merely to round-robin healthy
    production traffic.
    """
    now = time.monotonic()
    cap = _max_inflight_per_endpoint()

    with _ENDPOINT_LOCK:
        available: list[tuple[int, float, int, str]] = []
        public_available: list[tuple[int, float, int, str]] = []
        cooling = 0
        busy = 0

        for index, url in enumerate(urls):
            if url in tried:
                continue
            state = _state_for(url)
            if float(state["cooldown_until"]) > now:
                cooling += 1
                continue
            inflight = int(state["inflight"])
            if inflight >= cap:
                busy += 1
                continue
            item = (inflight, float(state["last_started"]), index, url)
            if url == _sol.DEFAULT_RPC:
                public_available.append(item)
            else:
                available.append(item)

        pool = available or public_available
        if not pool:
            reason = "cooling" if cooling and not busy else "busy" if busy and not cooling else "cooling_or_busy"
            return None, reason

        # Spread simultaneous worker bursts across configured providers by choosing
        # the least-busy, least-recently-started endpoint. Stable index is the final
        # tiebreaker so configured priority remains deterministic.
        _, _, _, url = min(pool)
        state = _state_for(url)
        state["inflight"] = int(state["inflight"]) + 1
        state["last_started"] = now
        return url, "ok"


def _release_endpoint(
    url: str,
    *,
    success: bool,
    error: SolanaRpcEndpointError | None = None,
) -> None:
    now = time.monotonic()
    with _ENDPOINT_LOCK:
        state = _state_for(url)
        state["inflight"] = max(0, int(state["inflight"]) - 1)

        if success:
            state["cooldown_until"] = 0.0
            state["rate_limit_strikes"] = 0
            return

        if error is None or not error.transient:
            return

        if _is_rate_limited(error):
            strikes = min(8, int(state["rate_limit_strikes"]) + 1)
            state["rate_limit_strikes"] = strikes
            cooldown = _rate_limit_base_cooldown() * (2 ** (strikes - 1))
            cooldown = max(cooldown, float(error.retry_after_seconds or 0.0))
            cooldown = min(cooldown, _rate_limit_max_cooldown())
            state["cooldown_until"] = max(float(state["cooldown_until"]), now + cooldown)
        else:
            state["cooldown_until"] = max(
                float(state["cooldown_until"]),
                now + _transient_cooldown(),
            )


def _reset_endpoint_health_for_tests() -> None:
    with _ENDPOINT_LOCK:
        _ENDPOINT_STATE.clear()


def rpc_failover(app, method: str, params: list):
    urls = _candidate_urls(app)
    if not urls:
        raise SolanaRpcEndpointError(method, "no RPC endpoint configured", transient=False)

    tried: set[str] = set()
    last: SolanaRpcEndpointError | None = None

    while len(tried) < len(urls):
        url, reason = _acquire_endpoint(urls, tried)
        if url is None:
            detail = (
                "all RPC endpoints temporarily cooling down"
                if reason == "cooling"
                else "all RPC endpoints temporarily busy"
                if reason == "busy"
                else "all RPC endpoints temporarily cooling down or busy"
            )
            raise SolanaRpcEndpointError(method, detail, transient=True)

        tried.add(url)
        try:
            result = _post_one(url, method, params)
        except SolanaRpcEndpointError as exc:
            _release_endpoint(url, success=False, error=exc)
            last = exc
            if not exc.transient:
                raise
            continue
        except Exception:
            # Defensive bookkeeping only. _post_one currently normalises all
            # endpoint faults, but never leave an endpoint permanently in-flight.
            _release_endpoint(url, success=False)
            raise
        else:
            _release_endpoint(url, success=True)
            return result

    if last is not None:
        raise last
    raise SolanaRpcEndpointError(method, "all RPC endpoints unavailable", transient=True)


def install() -> None:
    _sol._rpc = rpc_failover
    print(
        "[solana-rpc-failover] multi_endpoint=true helius_fallback=true "
        "public_rpc_last=true rate_limit_cooldown=true burst_spread=true "
        "secret_safe_errors=true"
    )


install()
