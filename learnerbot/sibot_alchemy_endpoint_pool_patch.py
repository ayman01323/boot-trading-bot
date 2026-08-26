from __future__ import annotations

"""Bounded endpoint failover inside the audited EVM history pipeline.

The final SiBot history refresher remains exactly
``sibot_alchemy_trace_progress_patch.refresh_wallet_history``. This module changes
only the two dynamic inner calls owned by that wrapper: the non-trace history path
and the progressive BSC/Arbitrum path. Each may select from all already-configured
Alchemy HTTP endpoints for the chain, apply a local per-endpoint circuit breaker,
and retry a small number of distinct endpoints only for clear transient/provider
failures.

Progressive ``AlchemyHistoryProgress`` yields never switch endpoints. Raw RPC URLs
remain in-process only and are never logged or persisted here. This is history and
research plumbing only: it never signs, broadcasts, changes LIVE/AUTO/ARMED, changes
trade size, or changes any leader/execution risk gate.
"""

import csv
import hashlib
import threading
import time
from pathlib import Path

from . import sibot as _sibot
from . import sibot_alchemy_history_patch as _alchemy
from . import sibot_alchemy_retry_queue_patch as _retry
from . import sibot_alchemy_trace_progress_patch as _trace
from . import sibot_legacy_backlog_drainer_patch as _drainer

_PREV_NONTRACE_REFRESH = _trace._PREV_REFRESH_WALLET_HISTORY
_PREV_PROGRESSIVE_REFRESH = _trace._refresh_progressive
_TLS = threading.local()
_STATE_LOCK = threading.Lock()
_COOLDOWN_UNTIL: dict[tuple[int, str], float] = {}
_PRESSURE_COUNT: dict[tuple[int, str], int] = {}
_LAST_SUCCESS: dict[int, str] = {}

_MAX_FAILOVER_ENDPOINTS = 3
_RATE_LIMIT_BASE_SECONDS = 120
_RATE_LIMIT_MAX_SECONDS = 15 * 60
_TRANSPORT_COOLDOWN_SECONDS = 30


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _priority(value):
    try:
        return int(float(value))
    except Exception:
        return 999


def _endpoint_id(url: str) -> str:
    return hashlib.sha256(str(url or "").encode("utf-8")).hexdigest()[:16]


def alchemy_rpc_urls(app, chain_id: int) -> list[str]:
    """Return enabled Alchemy HTTP endpoints in configured priority order."""
    path = Path(app.csv_dir) / "rpc_endpoints.csv"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        return []

    candidates: list[tuple[int, str]] = []
    seen = set()
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
        if "$" in url or "alchemy.com" not in low or url in seen:
            continue
        seen.add(url)
        candidates.append((_priority(row.get("priority")), url))
    candidates.sort(key=lambda item: item[0])
    return [url for _priority_value, url in candidates]


def _cooling(chain_id: int, url: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else float(now)
    key = (int(chain_id), _endpoint_id(url))
    with _STATE_LOCK:
        return float(_COOLDOWN_UNTIL.get(key, 0.0)) > now


def _ordered_available_urls(app, chain_id: int) -> list[str]:
    urls = alchemy_rpc_urls(app, chain_id)
    if not urls:
        return []
    with _STATE_LOCK:
        preferred_id = _LAST_SUCCESS.get(int(chain_id), "")
    if preferred_id:
        urls.sort(key=lambda url: 0 if _endpoint_id(url) == preferred_id else 1)
    return [url for url in urls if not _cooling(chain_id, url)]


def alchemy_rpc_url(app, chain_id: int) -> str:
    """Compatibility selector resolved dynamically by existing history code."""
    forced = str(getattr(_TLS, "forced_url", "") or "")
    if forced:
        return forced
    available = _ordered_available_urls(app, int(chain_id))
    if available:
        return available[0]
    # Keep provider/config detection truthful even during a temporary circuit.
    urls = alchemy_rpc_urls(app, int(chain_id))
    return urls[0] if urls else ""


def _error_text(result) -> str:
    return str(result.get("error") or "") if isinstance(result, dict) else ""


def _provider_pressure(error: str) -> bool:
    text = str(error or "")
    if _retry._retryable_alchemy_error(text):
        return True
    low = text.lower()
    return "alchemy" in low and any(
        marker in low
        for marker in (
            "http 429",
            "rpc 429",
            "rate limit",
            "compute units per second",
            "retries exhausted",
        )
    )


def _transport_failure(error: str) -> bool:
    low = str(error or "").lower()
    if "alchemy" not in low:
        return False
    return any(
        marker in low
        for marker in (
            "transport",
            "timeout",
            "timed out",
            "connection",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
        )
    )


def _mark_pressure(chain_id: int, url: str) -> int:
    key = (int(chain_id), _endpoint_id(url))
    with _STATE_LOCK:
        count = int(_PRESSURE_COUNT.get(key, 0)) + 1
        _PRESSURE_COUNT[key] = count
        delay = min(_RATE_LIMIT_MAX_SECONDS, _RATE_LIMIT_BASE_SECONDS * (2 ** max(0, count - 1)))
        _COOLDOWN_UNTIL[key] = time.monotonic() + delay
    return int(delay)


def _mark_transport(chain_id: int, url: str) -> None:
    key = (int(chain_id), _endpoint_id(url))
    with _STATE_LOCK:
        _COOLDOWN_UNTIL[key] = time.monotonic() + _TRANSPORT_COOLDOWN_SECONDS


def _mark_success(chain_id: int, url: str) -> None:
    endpoint = _endpoint_id(url)
    key = (int(chain_id), endpoint)
    with _STATE_LOCK:
        _PRESSURE_COUNT.pop(key, None)
        _COOLDOWN_UNTIL.pop(key, None)
        _LAST_SUCCESS[int(chain_id)] = endpoint


def endpoint_pool_health(app, chain_id: int) -> dict:
    """Return only redacted endpoint-pool telemetry."""
    urls = alchemy_rpc_urls(app, int(chain_id))
    cooling = sum(1 for url in urls if _cooling(int(chain_id), url))
    with _STATE_LOCK:
        preferred = bool(_LAST_SUCCESS.get(int(chain_id)))
    return {
        "configured": len(urls),
        "available_now": max(0, len(urls) - cooling),
        "cooling": cooling,
        "has_preferred_success": preferred,
        "identifiers_redacted": True,
    }


def _with_endpoint_pool(original, app, chain, wallet: str):
    """Run one existing history stage with bounded distinct-endpoint failover."""
    chain_id = int(chain.chain_id)
    urls = _ordered_available_urls(app, chain_id)
    if not urls:
        configured = alchemy_rpc_urls(app, chain_id)
        if not configured:
            return original(app, chain, wallet)
        return _alchemy._store_error(
            app,
            chain,
            wallet,
            int(time.time()),
            "AlchemyHistoryError: endpoint pool cooling down after provider rate limiting",
        )

    last_result = None
    attempts = 0
    for url in urls[:_MAX_FAILOVER_ENDPOINTS]:
        attempts += 1
        _TLS.forced_url = url
        try:
            result = original(app, chain, wallet)
        finally:
            _TLS.forced_url = ""

        last_result = result
        error = _error_text(result)
        if not error:
            _mark_success(chain_id, url)
            if isinstance(result, dict):
                result = dict(result)
                result["endpoint_failover_attempts"] = attempts
                result["endpoint_identifiers_redacted"] = True
            return result

        # Cooperative progress is not an endpoint failure. Keep using its cached
        # context/trace evidence on the next scheduled wallet turn.
        if error.startswith("AlchemyHistoryProgress:"):
            return result
        if _provider_pressure(error):
            _mark_pressure(chain_id, url)
            continue
        if _transport_failure(error):
            _mark_transport(chain_id, url)
            continue
        return result

    return last_result if last_result is not None else original(app, chain, wallet)


def refresh_nontrace_with_endpoint_pool(app, chain, wallet: str):
    return _with_endpoint_pool(_PREV_NONTRACE_REFRESH, app, chain, wallet)


def refresh_progressive_with_endpoint_pool(app, chain, wallet: str):
    return _with_endpoint_pool(_PREV_PROGRESSIVE_REFRESH, app, chain, wallet)


def install() -> None:
    if getattr(_sibot, "_alchemy_endpoint_pool_patch_installed", False):
        return

    # Keep the final audited refresher identity unchanged. Only its dynamic inner
    # stages and endpoint resolver are replaced.
    _alchemy.alchemy_rpc_url = alchemy_rpc_url
    _trace._PREV_REFRESH_WALLET_HISTORY = refresh_nontrace_with_endpoint_pool
    _trace._refresh_progressive = refresh_progressive_with_endpoint_pool

    # Both ranked and background recovery queues should wait long enough after an
    # account/provider 429. This changes history scheduling only.
    _retry._TRANSIENT_RETRY_COOLDOWN_SECONDS = max(
        int(getattr(_retry, "_TRANSIENT_RETRY_COOLDOWN_SECONDS", 60)), 180
    )
    _drainer._DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = max(
        int(getattr(_drainer, "_DEFAULT_RATE_LIMIT_BACKOFF_SECONDS", 60)), 180
    )
    _drainer._DEFAULT_MAX_BACKOFF_SECONDS = max(
        int(getattr(_drainer, "_DEFAULT_MAX_BACKOFF_SECONDS", 900)), 1800
    )
    _drainer._MIN_TRANSIENT_RETRY_AGE_SECONDS = max(
        int(getattr(_drainer, "_MIN_TRANSIENT_RETRY_AGE_SECONDS", 60)), 180
    )

    _sibot.alchemy_history_rpc_url = alchemy_rpc_url
    _sibot.alchemy_history_endpoint_pool_health = endpoint_pool_health
    _sibot._alchemy_endpoint_pool_patch_installed = True
    print(
        "[sibot-alchemy-endpoint-pool] installed=true placement=inside_trace_progress "
        "failover<=3 per_endpoint_circuit_breaker=true retry_cooldown>=180s "
        "rpc_identifiers_redacted=true final_refresh_identity_unchanged=true "
        "execution_safety=unchanged",
        flush=True,
    )


install()
