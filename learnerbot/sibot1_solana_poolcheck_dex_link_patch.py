from __future__ import annotations

import html
import inspect
import threading
from urllib.parse import quote

from . import sibot1_solana_live_bridge_patch as _bridge

# Reporting-only patch: append a direct DexView token chart link to Solana LIVE
# PoolCheck block alerts. It changes no PoolCheck decision, trading threshold,
# signer rule, quote, simulation, execution or broadcast behaviour.
#
# DexView routes token pages by chain slug plus token address/mint, for example:
#   https://www.dexview.com/robinhood/<token-address>
# and for SiBot 1 Solana alerts:
#   https://www.dexview.com/solana/<mint>
# Building the link is local and adds no API/RPC request.
#
# Some bridge call paths can reach _notify without the normal candidate wrapper.
# For PoolCheck alerts only, fall back to nearby bridge call-frame locals so a
# real block alert still gets its token viewer whenever a mint is available.

_PREV_NOTIFY = _bridge._notify
_PREV_PROCESS_CANDIDATE = _bridge._process_candidate
_TLS = threading.local()
_INSTALLED = False
_ALERT_MARKER = "SiBot 1 Solana candidate blocked by LIVE PoolCheck"


def quick_view_url(mint: str) -> str:
    """Return a direct DexView Solana token URL for a mint; no API call required."""
    value = str(mint or "").strip()
    if not value:
        return ""
    return "https://www.dexview.com/solana/" + quote(value, safe="")


# Backwards-compatible helper name for existing tests/importers.
def dex_view_url(mint: str) -> str:
    return quick_view_url(mint)


def _mint_from_call_context() -> str:
    """Recover the mint from nearby bridge frames only when an alert needs it."""
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        for _ in range(8):
            if frame is None:
                break
            local_mint = str(frame.f_locals.get("mint") or "").strip()
            if local_mint:
                return local_mint
            candidate = frame.f_locals.get("candidate")
            if isinstance(candidate, dict):
                value = str(
                    candidate.get("asset_out")
                    or candidate.get("asset")
                    or candidate.get("token")
                    or ""
                ).strip()
                if value:
                    return value
            frame = frame.f_back
    finally:
        del frame
    return ""


def _notify_with_quick_view(app, tid, text):
    rendered = str(text or "")
    if _ALERT_MARKER not in rendered or "dexview.com/solana/" in rendered:
        return _PREV_NOTIFY(app, tid, rendered)

    mint = str(getattr(_TLS, "mint", "") or "").strip()
    if not mint:
        mint = _mint_from_call_context()
    url = quick_view_url(mint)
    if url:
        rendered += (
            "\n📈 <a href=\""
            + html.escape(url, quote=True)
            + "\">DEX View</a>"
        )
    return _PREV_NOTIFY(app, tid, rendered)


def _process_candidate_with_quick_view_context(app, tid, candidate):
    previous = getattr(_TLS, "mint", None)
    _TLS.mint = str(
        candidate.get("asset_out")
        or candidate.get("asset")
        or candidate.get("token")
        or ""
    ).strip()
    try:
        return _PREV_PROCESS_CANDIDATE(app, tid, candidate)
    finally:
        if previous is None:
            try:
                delattr(_TLS, "mint")
            except AttributeError:
                pass
        else:
            _TLS.mint = previous


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _bridge._notify = _notify_with_quick_view
    _bridge._process_candidate = _process_candidate_with_quick_view_context
    _INSTALLED = True
    print(
        "[sibot1-solana-poolcheck-quick-view] installed=true "
        "viewer=dexview direct_mint=true alert_only=true "
        "safety_gates=unchanged"
    )


install()
