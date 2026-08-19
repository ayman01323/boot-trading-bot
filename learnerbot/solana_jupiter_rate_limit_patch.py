from __future__ import annotations

import email.utils
import threading
import time
from datetime import datetime, timezone

from . import solana_execution_efficiency_patch as _eff
from . import solana_jupiter_order_recovery_patch as _recovery
from . import solana_live_executor as _exec
from . import solana_sibot as _sol


# Rate-limit recovery is deliberately bounded.  /order returns an unsigned order;
# retrying this HTTP request cannot duplicate a broadcast because signing/submission
# happens later.  No amount, slippage, fee, router or economic guard is changed.
_sol.DEFAULTS.update({
    "live_jupiter_429_inline_retries": (
        "2",
        "Maximum inline Jupiter HTTP-429 retries before deferring to the normal LIVE monitor",
    ),
    "live_jupiter_429_base_delay_seconds": (
        "1",
        "Initial Jupiter HTTP-429 retry delay when Retry-After is absent",
    ),
    "live_jupiter_429_max_inline_delay_seconds": (
        "8",
        "Maximum single inline wait for Jupiter HTTP-429 recovery",
    ),
})

_RATE_LOCK = threading.Lock()
_RATE_LIMIT_UNTIL = 0.0


def _number(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _integer(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _settings(executor) -> tuple[int, float, float]:
    try:
        cfg = dict(_eff._cfg(executor.app) or {})
    except Exception:
        cfg = {}
    retries = max(0, min(4, _integer(cfg.get("live_jupiter_429_inline_retries"), 2)))
    base = max(0.25, min(10.0, _number(cfg.get("live_jupiter_429_base_delay_seconds"), 1.0)))
    max_inline = max(base, min(30.0, _number(cfg.get("live_jupiter_429_max_inline_delay_seconds"), 8.0)))
    return retries, base, max_inline


def _retry_after_seconds(response) -> float | None:
    try:
        headers = getattr(response, "headers", {}) or {}
        raw = str(headers.get("Retry-After") or headers.get("retry-after") or "").strip()
    except Exception:
        raw = ""
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except Exception:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def _set_cooldown(seconds: float) -> float:
    global _RATE_LIMIT_UNTIL
    seconds = max(0.0, float(seconds))
    target = time.monotonic() + seconds
    with _RATE_LOCK:
        _RATE_LIMIT_UNTIL = max(_RATE_LIMIT_UNTIL, target)
        return _RATE_LIMIT_UNTIL


def _cooldown_remaining() -> float:
    with _RATE_LOCK:
        return max(0.0, _RATE_LIMIT_UNTIL - time.monotonic())


def _clear_elapsed_cooldown() -> None:
    global _RATE_LIMIT_UNTIL
    with _RATE_LOCK:
        if _RATE_LIMIT_UNTIL <= time.monotonic():
            _RATE_LIMIT_UNTIL = 0.0


def _wait_existing_cooldown(max_inline: float) -> float:
    remaining = _cooldown_remaining()
    if remaining <= 0:
        return 0.0
    if remaining > max_inline:
        raise _exec.SolanaLiveError(
            f"Jupiter rate-limit cooldown active; retry_after={remaining:.2f}s exceeds bounded inline wait {max_inline:.2f}s"
        )
    time.sleep(remaining)
    _clear_elapsed_cooldown()
    return remaining


def get_json_with_bounded_429_recovery(executor, params: dict, *, context: str) -> dict:
    """Retry pre-signing Jupiter HTTP 429s without changing trade economics.

    A process-wide cooldown prevents multiple Telegram users/positions from immediately
    repeating the same request storm.  Retry-After is honoured when it fits the bounded
    inline wait.  Longer provider cooldowns are surfaced to the caller so the existing
    position monitor can retry later instead of blocking a worker for a long period.
    """
    max_retries, base_delay, max_inline = _settings(executor)
    retries = 0
    total_wait = 0.0

    while True:
        total_wait += _wait_existing_cooldown(max_inline)
        response = _recovery.requests.get(
            f"{_exec.JUPITER_BASE}/order",
            params=dict(params or {}),
            headers=_eff._headers(executor),
            timeout=30,
        )
        status = int(getattr(response, "status_code", 200) or 200)

        if status == 429:
            body = _recovery._response_error_text(response)
            retry_after = _retry_after_seconds(response)
            if retries >= max_retries:
                raise _exec.SolanaLiveError(
                    f"Jupiter {context} HTTP 429 after {retries} bounded retries: {body}"
                )

            delay = retry_after if retry_after is not None else min(max_inline, base_delay * (2 ** retries))
            delay = max(0.25, float(delay))
            _set_cooldown(delay)
            if delay > max_inline:
                raise _exec.SolanaLiveError(
                    f"Jupiter {context} HTTP 429: {body}; Retry-After={delay:.2f}s exceeds bounded inline wait {max_inline:.2f}s"
                )

            time.sleep(delay)
            total_wait += delay
            _clear_elapsed_cooldown()
            retries += 1
            continue

        if status >= 400:
            raise _exec.SolanaLiveError(
                f"Jupiter {context} HTTP {status}: {_recovery._response_error_text(response)}"
            )
        try:
            data = dict(response.json() or {})
        except Exception as exc:
            raise _exec.SolanaLiveError(
                f"Jupiter {context} returned invalid JSON (HTTP {status})"
            ) from exc

        if retries:
            data["_jupiter_rate_limit_recovered"] = True
            data["_jupiter_429_retries"] = int(retries)
            data["_jupiter_429_wait_seconds"] = round(total_wait, 3)
        return data


def install():
    # Both request_quote_with_error_body() and order_with_http400_recovery() resolve
    # _get_json from the recovery module at call time, so one canonical replacement
    # covers quote/order requests while preserving the existing HTTP-400 safe retry.
    _recovery._get_json = get_json_with_bounded_429_recovery
    print(
        "[solana-jupiter-429-recovery] shared_cooldown=true retries=bounded "
        "retry_after_respected=true economics_unchanged=true pre_signing_only=true"
    )


install()
