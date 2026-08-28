from __future__ import annotations

"""Complete market context for every position-specific learner Telegram message.

Adds token name/symbol, current position SOL+USD value, unrealised and realised
SOL+USD P&L, primary pool/DEX identity, existing pool-open/current/change data,
and Dex Viewer. The shared DexScreener cache is reused; trading logic is untouched.
"""

import html
from decimal import Decimal
from urllib.parse import quote as urlquote

from . import solana_pool_risk_gate as _pool
from . import solana_sibot as _sol
from . import telegram_learner_position_update_patch as _position

_PREV_POOL_CONTEXT = _position.pool_context_html


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


def _identity(mint: str, cfg: dict) -> dict:
    if not mint:
        return {}
    try:
        encoded = urlquote(str(mint), safe="")
        ttl = float(max(15, _sol._int(cfg.get("live_pool_dex_cache_seconds"), 60)))
        pairs, _cached = _pool._fetch_json(
            "dexscreener",
            _pool._DEX_URL.format(mint=encoded),
            str(mint),
            ttl,
            _pool._timeout(cfg),
        )
        if not isinstance(pairs, list):
            return {}
        pairs = [
            p for p in pairs
            if isinstance(p, dict)
            and str(p.get("chainId") or "solana").lower() == "solana"
        ]
        if not pairs:
            return {}
        best = max(pairs, key=lambda p: _pool._liq_usd(p))
        base = dict(best.get("baseToken") or {})
        quote = dict(best.get("quoteToken") or {})
        token = base if str(base.get("address") or "") == str(mint) else quote
        if str(token.get("address") or "") != str(mint):
            token = base
        return {
            "name": str(token.get("name") or "").strip(),
            "symbol": str(token.get("symbol") or "").strip(),
            "dex": str(best.get("dexId") or "").strip(),
            "pair": str(best.get("pairAddress") or "").strip(),
        }
    except Exception:
        return {}


def _sol_usd_line(label: str, sol_value: Decimal, sol_price: Decimal, *, signed: bool = False) -> str:
    sol_value = _d(sol_value)
    sol_text = f"{sol_value:+,.9f}" if signed else f"{sol_value:,.9f}"
    if sol_price > 0:
        usd = sol_value * sol_price
        usd_text = _position._usd_text(usd)
        return f"{label}: <b>{sol_text} SOL</b> (≈ <b>{usd_text}</b>)"
    return f"{label}: <b>{sol_text} SOL</b> (USD unavailable)"


def complete_pool_context_html(app, position: dict, current: dict | None = None) -> str:
    position = dict(position or {})
    mint = str(position.get("mint") or "")
    cfg = _sol.settings(app)
    sol_price = _position._sol_usd(app)
    ident = _identity(mint, cfg)

    symbol = html.escape(str(ident.get("symbol") or "UNKNOWN"))
    name = html.escape(str(ident.get("name") or "Token name unavailable"))
    dex = html.escape(str(ident.get("dex") or "DEX unavailable"))
    pair = html.escape(str(ident.get("pair") or "Pool address unavailable"))

    current_exit = _d(position.get("current_exit_sol"), 0)
    unrealised = _d(position.get("unrealised_net_sol"), 0)
    realised = _d(position.get("realised_net_sol"), 0)

    lines = [
        "🪙 <b>TOKEN / POSITION</b>",
        f"🏷 Token: <b>{symbol}</b> — <b>{name}</b>",
        f"🧾 Mint: <code>{html.escape(mint)}</code>",
        "💼 " + _sol_usd_line("Current position", current_exit, sol_price),
        "📊 " + _sol_usd_line("Unrealised P&L", unrealised, sol_price, signed=True),
        "✅ " + _sol_usd_line("Realised P&L", realised, sol_price, signed=True),
        f"🏊 Pool / DEX: <b>{dex}</b> • <code>{pair}</code>",
    ]

    original = _PREV_POOL_CONTEXT(app, position, current)
    if original:
        lines.append(original)
    return "\n".join(lines)


def install() -> None:
    if getattr(_position, "_learner_complete_market_context_installed", False):
        return
    _position.pool_context_html = complete_pool_context_html
    _position._learner_complete_market_context_installed = True
    print(
        "[learner-complete-market-context] active=true token_name=true token_symbol=true "
        "dex_viewer=true pool=true position_sol=true position_usd=true pnl_sol=true pnl_usd=true",
        flush=True,
    )


install()
