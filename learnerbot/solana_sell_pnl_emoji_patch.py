from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from . import solana_live_patch as _live

# Telegram displays Solana P&L to 9 decimal places. Treat values smaller than
# half of the last displayed unit as break-even so the emoji agrees with the
# number the user actually sees.
_BREAK_EVEN_EPSILON = Decimal("0.0000000005")
_PNL_RE = re.compile(
    r"(?P<label>Net on sold portion|Realised net P&L):\s*"
    r"(?P<prefix><b>)?(?P<amount>[+-]?(?:\d+(?:\.\d+)?|\.\d+))\s+SOL(?P<suffix></b>)?",
    re.IGNORECASE,
)


def _pnl_emoji(value: Decimal) -> str:
    if value > _BREAK_EVEN_EPSILON:
        return "💚"
    if value < -_BREAK_EVEN_EPSILON:
        return "❤️"
    return "🍉"


def decorate_sell_pnl(text: str) -> str:
    """Show profit/loss/break-even emoji beside realised Solana SELL P&L."""
    if not isinstance(text, str) or "SOLANA LIVE SELL" not in text.upper():
        return text

    def repl(match: re.Match) -> str:
        try:
            value = Decimal(match.group("amount"))
        except (InvalidOperation, ValueError):
            return match.group(0)
        emoji = _pnl_emoji(value)
        prefix = match.group("prefix") or ""
        suffix = match.group("suffix") or ""
        return f"Realised net P&L: {emoji} {prefix}{match.group('amount')} SOL{suffix}"

    return _PNL_RE.sub(repl, text, count=1)


def _wrap_notify(fn):
    if not callable(fn) or getattr(fn, "_solana_sell_pnl_emoji_wrapped", False):
        return fn

    def wrapped(app, tid, text):
        return fn(app, tid, decorate_sell_pnl(text))

    wrapped.__name__ = getattr(fn, "__name__", "solana_sell_pnl_notify")
    wrapped._solana_sell_pnl_emoji_wrapped = True
    return wrapped


def install():
    _live._notify = _wrap_notify(_live._notify)
    print("[solana-sell-pnl] profit=💚 loss=❤️ break_even=🍉")


install()
