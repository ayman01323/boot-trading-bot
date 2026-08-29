from __future__ import annotations

"""Presentation-only Dexview link for confirmed Solana LIVE BUY messages.

This patch does not change trading decisions, sizing, signing, simulation,
PoolCheck, execution, position state, or exits. It only enriches an existing
Telegram BUY confirmation after the transaction is already confirmed.
"""

import html
import re

from . import solana_live_patch as _live

_PREV_NOTIFY = _live._notify
_TOKEN_RE = re.compile(r"Token:\s*<code>([^<]+)</code>")


def _notify_with_dexview(app, telegram_id, text: str):
    message = str(text or "")
    if "Solana LIVE BUY confirmed" in message and "Dexview:" not in message:
        match = _TOKEN_RE.search(message)
        if match:
            mint = match.group(1).strip()
            if mint:
                link = (
                    'Dexview: <a href="https://www.dexview.com/solana/%s">Open Dexview</a>'
                    % html.escape(mint, quote=True)
                )
                marker = "\nReceived raw:"
                if marker in message:
                    message = message.replace(marker, "\n" + link + marker, 1)
                else:
                    message += "\n" + link
    return _PREV_NOTIFY(app, telegram_id, message)


def install() -> None:
    if getattr(_live, "_solana_buy_dexview_patch_installed", False):
        return
    _live._notify = _notify_with_dexview
    _live._solana_buy_dexview_patch_installed = True
    print(
        "[solana-buy-dexview] presentation_only=true "
        "buy_confirmed_link=true trading_logic_unchanged=true"
    )


install()
