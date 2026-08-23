from __future__ import annotations

import threading
import time
from decimal import Decimal

from . import solana_sibot as _sol

_PREV_VALIDATE = _sol._validate_shadow_entry
_LOCK = threading.Lock()
_CACHE = {}
_TTL_SECONDS = 3.0


def _key(event, allocation, cfg):
    return (
        str(event.get("signature") or ""),
        str(event.get("mint") or ""),
        str(event.get("token_amount_raw") or ""),
        str(event.get("sol_amount") or ""),
        str(Decimal(str(allocation))),
        str(cfg.get("max_roundtrip_loss_pct") or ""),
        str(cfg.get("max_entry_deterioration_pct") or ""),
        str(cfg.get("live_entry_require_exit_liquidity_max_bps") or ""),
        str(cfg.get("live_emergency_exit_max_combined_bps") or ""),
        str(cfg.get("live_order_slippage_bps") or ""),
    )


def validate_entry_cached(app, event: dict, allocation_sol: Decimal, cfg: dict):
    # Signal age is user-independent but time-dependent, so always evaluate it
    # before consulting a cached quote result.
    age = max(0, int(time.time()) - int(event.get("event_ts") or 0))
    maximum = _sol._int(cfg.get("max_signal_age_seconds"), 30)
    if age > maximum:
        return False, f"stale signal {age}s", {}

    key = _key(event, allocation_sol, cfg)
    now = time.monotonic()
    with _LOCK:
        item = _CACHE.get(key)
        if item and now - item[0] <= _TTL_SECONDS:
            return item[1]

    # Exceptions are deliberately not cached: transient quote/RPC failures should
    # remain eligible for another bounded attempt while the signal is still fresh.
    result = _PREV_VALIDATE(app, event, allocation_sol, cfg)
    with _LOCK:
        _CACHE[key] = (time.monotonic(), result)
        if len(_CACHE) > 256:
            cutoff = time.monotonic() - 30
            for old_key, old_item in list(_CACHE.items()):
                if old_item[0] < cutoff:
                    _CACHE.pop(old_key, None)
    return result


def install():
    _sol._validate_shadow_entry = validate_entry_cached
    print("[solana-preflight-cache] ttl=3s exact_signal_allocation_risk_key=true")


install()
