from __future__ import annotations

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


def annotate_order_text(app, text: str) -> str:
    """Add best-effort USD equivalents to confirmed BUY/SELL order messages."""
    if not isinstance(text, str) or not text:
        return text
    upper = text.upper()
    if not any(marker in upper for marker in _ORDER_MARKERS):
        return text
    return _usd.annotate_text(app, text)


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
