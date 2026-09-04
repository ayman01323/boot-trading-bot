from __future__ import annotations

import re

from . import sibot as _sibot
from . import solana_live_patch as _sol_live
from . import telegram_usd_everywhere_patch as _usd

_ORDER_MARKERS = (
    "LIVE BUY",
    "SHADOW BUY",
    "LIVE SELL",
    "SHADOW SELL",
    "LIVE EXIT",
    "SHADOW EXIT",
    "BUY CONFIRMED",
    "SELL CONFIRMED",
)
_SOLANA_MINT_RE = re.compile(
    r"(?:Token|Mint):\s*<code>([1-9A-HJ-NP-Za-km-z]{32,44})</code>",
    re.IGNORECASE,
)


def _append_dexview_link(text: str) -> str:
    """Append a dynamic DexView token link to confirmed Solana/Learner BUY alerts."""
    if not isinstance(text, str) or not text:
        return text
    lower = text.lower()
    if "dexview.com/solana/" in lower:
        return text
    upper = text.upper()
    if "BUY" not in upper or ("SOLANA" not in upper and "LEARNER" not in upper):
        return text
    match = _SOLANA_MINT_RE.search(text)
    if not match:
        return text
    mint = match.group(1)
    return text + f'\n🔎 <a href="https://www.dexview.com/solana/{mint}">DEX View</a>'


def annotate_order_text(app, text: str) -> str:
    """Add best-effort USD equivalents and Solana DexView links to order messages."""
    if not isinstance(text, str) or not text:
        return text
    upper = text.upper()
    if not any(marker in upper for marker in _ORDER_MARKERS):
        return text
    annotated = _usd.annotate_text(app, text)
    return _append_dexview_link(annotated)


def _wrap_notify(fn):
    if not callable(fn) or getattr(fn, "_order_usd_wrapped", False):
        return fn

    def wrapped(app, tid, text):
        return fn(app, tid, annotate_order_text(app, text))

    wrapped.__name__ = getattr(fn, "__name__", "order_usd_notify")
    wrapped._order_usd_wrapped = True
    return wrapped


def install():
    _sibot._notify = _wrap_notify(_sibot._notify)
    _sol_live._notify = _wrap_notify(_sol_live._notify)


install()
