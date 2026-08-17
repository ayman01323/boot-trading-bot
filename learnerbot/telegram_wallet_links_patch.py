from __future__ import annotations

import html

from . import telegram_sibot_patch as _sp
from .sibot import leader_rows, ranking_rows

_original_leaders_page = _sp.leaders_page
_original_top20_page = _sp.top20_page


def wallet_explorer_link(chain, address: str) -> str:
    """Return a Telegram HTML wallet link using the configured chain explorer."""
    address = str(address or "").strip()
    label = html.escape(_sp._short(address))
    explorer = str(getattr(chain, "explorer_url", "") or "").strip().rstrip("/") if chain else ""
    if not address or not explorer:
        return f"<code>{label}</code>"
    url = f"{explorer}/address/{address}"
    return f'<a href="{html.escape(url, quote=True)}">🔎 {label}</a>'


def _replace_wallet(text: str, chain, address: str) -> str:
    old = f"<code>{html.escape(_sp._short(address))}</code>"
    return str(text).replace(old, wallet_explorer_link(chain, address))


def leaders_page(app, tid, chain=None):
    text = _original_leaders_page(app, tid, chain)
    target = _sp._chain(app, chain) if chain else None
    rows = leader_rows(app, tid, target.chain_id if target else None)
    for row in rows:
        c = _sp._chain(app, row.get("chain_id"))
        text = _replace_wallet(text, c, row.get("wallet") or "")
    return text


def top20_page(app, tid, chain):
    text = _original_top20_page(app, tid, chain)
    target = _sp._chain(app, chain)
    if not target:
        return text
    for row in ranking_rows(app, tid, target.chain_id):
        text = _replace_wallet(text, target, row.get("wallet") or "")
    return text


def install():
    if getattr(_sp, "_wallet_explorer_links_installed", False):
        return
    _sp.leaders_page = leaders_page
    _sp.top20_page = top20_page
    _sp._wallet_explorer_links_installed = True


install()
