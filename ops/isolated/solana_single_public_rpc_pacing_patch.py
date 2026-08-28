from __future__ import annotations

"""Pace a single public Solana RPC endpoint for the isolated learner.

The isolated learner currently has only Solana's public mainnet endpoint.  The
standard multi-endpoint failover quarantines an endpoint after 429 responses;
with only one endpoint that creates long periods where every worker sees
"all RPC endpoints temporarily cooling down".  This overlay keeps the existing
failover implementation but shortens single-endpoint cooldowns, serialises HTTP
RPC calls, and retries transient cooling/rate-limit failures with bounded backoff.

It does not change transaction safety, signing, simulation, strategy rules, or
production runtime files.
"""

import os
import threading
import time

from . import solana_rpc_failover_patch as _rf
from . import solana_sibot as _sol

PROFILE = "SOLANA_SINGLE_PUBLIC_RPC_PACING_V1"
MIN_SPACING_SECONDS = 0.20
MAX_ATTEMPTS = 4

# The failover helpers read these values dynamically.  On isolated service restart
# endpoint health starts clean and these bounds prevent a single 429 from causing
# a 30-180 second total blackout when no alternate provider exists.
os.environ["SOLANA_RPC_429_COOLDOWN_SECONDS"] = "1"
os.environ["SOLANA_RPC_429_MAX_COOLDOWN_SECONDS"] = "3"
os.environ["SOLANA_RPC_TRANSIENT_COOLDOWN_SECONDS"] = "0.5"
os.environ["SOLANA_RPC_MAX_INFLIGHT_PER_ENDPOINT"] = "1"

_BASE_RPC = _sol._rpc
_GATE = threading.Lock()
_LAST_STARTED = 0.0


def _retryable(exc: Exception) -> bool:
    if not isinstance(exc, _rf.SolanaRpcEndpointError):
        return False
    if not bool(getattr(exc, "transient", False)):
        return False
    text = str(exc).lower()
    return (
        int(getattr(exc, "status_code", 0) or 0) == 429
        or "cooling" in text
        or "busy" in text
        or "rate limit" in text
        or "too many requests" in text
        or "temporarily unavailable" in text
    )


def rpc_single_public_paced(app, method: str, params: list):
    global _LAST_STARTED
    last_exc: Exception | None = None

    # Serialisation is deliberate for the one-public-endpoint configuration. It
    # avoids simultaneous discovery/leader workers creating a burst that causes a
    # 429 and then knocks every worker into the same cooldown window.
    with _GATE:
        for attempt in range(MAX_ATTEMPTS):
            now = time.monotonic()
            wait = MIN_SPACING_SECONDS - (now - _LAST_STARTED)
            if wait > 0:
                time.sleep(wait)
            _LAST_STARTED = time.monotonic()
            try:
                return _BASE_RPC(app, method, params)
            except Exception as exc:
                last_exc = exc
                if not _retryable(exc) or attempt >= MAX_ATTEMPTS - 1:
                    raise
                # First retry waits just past the shortened base cooldown; later
                # retries progressively spread load but remain below the 45s signal
                # freshness window.
                time.sleep(min(3.25, 1.15 + 0.75 * attempt))

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Solana RPC pacing failed without an exception")


def install() -> None:
    if getattr(_sol, "_single_public_rpc_pacing_installed", False):
        return
    _sol._rpc = rpc_single_public_paced
    _sol._single_public_rpc_pacing_installed = True
    print(
        "[solana-public-rpc-pacing] installed=true "
        f"profile={PROFILE} serial=true min_spacing={MIN_SPACING_SECONDS}s "
        "429_cooldown=1-3s attempts=4 execution_safety=unchanged"
    )


install()
