from __future__ import annotations

import html
import threading
from urllib.parse import quote

from . import sibot1_solana_live_bridge_patch as _bridge

# Reporting-only patch: append a clickable DexScreener view to Solana LIVE
# PoolCheck block alerts. It changes no PoolCheck decision, trading threshold,
# signer rule, quote, simulation, execution or broadcast behaviour.

_PREV_NOTIFY = _bridge._notify
_PREV_PROCESS_CANDIDATE = _bridge._process_candidate
_TLS = threading.local()
_INSTALLED = False
_ALERT_MARKER = "SiBot 1 Solana candidate blocked by LIVE PoolCheck"


def dex_view_url(mint: str) -> str:
    """Return a DexScreener search URL for a Solana mint without any API call."""
    value = str(mint or "").strip()
    if not value:
        return ""
    return "https://dexscreener.com/search?q=" + quote(value, safe="")


def _notify_with_dex_view(app, tid, text):
    rendered = str(text or "")
    mint = str(getattr(_TLS, "mint", "") or "").strip()
    if mint and _ALERT_MARKER in rendered and "dexscreener.com/" not in rendered:
        url = dex_view_url(mint)
        if url:
            rendered += (
                "\n📊 <a href=\""
                + html.escape(url, quote=True)
                + "\">DEX View</a>"
            )
    return _PREV_NOTIFY(app, tid, rendered)


def _process_candidate_with_dex_context(app, tid, candidate):
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
    _bridge._notify = _notify_with_dex_view
    _bridge._process_candidate = _process_candidate_with_dex_context
    _INSTALLED = True
    print(
        "[sibot1-solana-poolcheck-dex-link] installed=true "
        "alert_only=true safety_gates=unchanged"
    )


install()
