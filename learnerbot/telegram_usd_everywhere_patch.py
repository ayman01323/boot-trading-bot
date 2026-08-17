from __future__ import annotations

import re
import time
from decimal import Decimal

from . import capital_dashboard as _cap
from . import telegram_dashboard_patch as _dash
from . import telegram_live_reporting_patch as _live
from . import telegram_multi_wallet_manager_patch as _multi
from . import telegram_sibot_intelligence_patch as _intel
from . import telegram_sibot_patch as _sibotui
from . import telegram_solana_everywhere_compat_patch as _compat
from . import telegram_solana_live_patch as _sollive
from . import telegram_solana_wallet_patch as _solwallet
from . import telegram_ui as _ui
from .config import load_chains

# Presentation-only layer. It never changes trading settings, wallets or execution.
# It adds current USD equivalents beside asset-denominated values already rendered
# by the Telegram UI. Prices are best-effort and cached; unavailable prices are
# never fabricated.

_STABLE = {"USDC", "USDC.E", "USDT", "USDT.E", "DAI", "FDUSD", "TUSD", "USDP", "BUSD"}
_PRICE_CACHE = {"ts": 0.0, "global": {}, "by_chain": {}, "chains": []}
_NUM_SYMBOL = re.compile(r"(?P<amount>[+-]?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?)\s+(?P<symbol>[A-Za-z][A-Za-z0-9.]{1,15})(?P<rate>/h)?")
_TAGS = re.compile(r"<[^>]+>")


def _dec(v, default="0") -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(str(default))


def _fmt_usd(v: Decimal) -> str:
    v = _dec(v)
    a = abs(v)
    if a >= Decimal("1000"):
        return f"${v:,.2f}"
    if a >= Decimal("1"):
        return f"${v:,.2f}"
    if a >= Decimal("0.01"):
        return f"${v:,.4f}".rstrip("0").rstrip(".")
    if a == 0:
        return "$0.00"
    return f"${v:,.6f}".rstrip("0").rstrip(".")


def _merge_price(target: dict[str, Decimal], symbol: str, price) -> None:
    symbol = str(symbol or "").upper().strip()
    p = _dec(price, 0)
    if not symbol or p <= 0:
        return
    old = target.get(symbol)
    if old is not None and old <= 0:
        target[symbol] = p
        return
    if old is not None and old > 0:
        diff = abs(p - old) / old
        if diff > Decimal("0.05"):
            target.pop(symbol, None)
            return
    target[symbol] = p


def _known_unpriced(target: dict[str, Decimal], symbol: str) -> None:
    symbol = str(symbol or "").upper().strip()
    if symbol and symbol not in target:
        target[symbol] = Decimal(0)


def _price_maps(app):
    now = time.time()
    if now - float(_PRICE_CACHE.get("ts") or 0) < 60 and _PRICE_CACHE.get("global"):
        return _PRICE_CACHE["global"], _PRICE_CACHE["by_chain"], _PRICE_CACHE["chains"]

    chains = load_chains(app, enabled_only=True)
    native = _cap._native_prices(chains)
    global_map: dict[str, Decimal] = {s: Decimal(1) for s in _STABLE}
    by_chain: dict[str, dict[str, Decimal]] = {}

    for c in chains:
        m: dict[str, Decimal] = {s: Decimal(1) for s in _STABLE}
        _known_unpriced(m, c.native_symbol)
        _known_unpriced(m, c.wrapped_base_symbol)
        np = native.get(c.slug)
        if np is not None:
            _merge_price(m, c.native_symbol, np)
            _merge_price(m, c.wrapped_base_symbol, np)
            _merge_price(global_map, c.native_symbol, np)
            _merge_price(global_map, c.wrapped_base_symbol, np)
        try:
            catalog = _cap._catalog(app, c)
            addresses = [str(x.get("address") or "") for x in catalog if x.get("address")]
            token_prices = _cap._token_prices(c, addresses)
            wrapped = str(c.wrapped_base_address or "").lower()
            for item in catalog:
                sym = str(item.get("symbol") or "").upper()
                addr = str(item.get("address") or "").lower()
                _known_unpriced(m, sym)
                p = token_prices.get(addr)
                if p is None and addr == wrapped:
                    p = np
                if p is None and sym in _STABLE:
                    p = Decimal(1)
                if p is not None:
                    _merge_price(m, sym, p)
                    _merge_price(global_map, sym, p)
        except Exception:
            pass
        by_chain[c.slug.lower()] = m
        by_chain[str(c.chain_id)] = m

    try:
        sp = _compat._sol_price_usd()
    except Exception:
        sp = None
    sol_map = {s: Decimal(1) for s in _STABLE}
    _known_unpriced(sol_map, "SOL")
    if sp is not None:
        _merge_price(sol_map, "SOL", sp)
        _merge_price(global_map, "SOL", sp)
    by_chain["solana"] = sol_map
    by_chain["sol"] = sol_map
    by_chain["-101"] = sol_map

    _PRICE_CACHE.update({"ts": now, "global": global_map, "by_chain": by_chain, "chains": chains})
    return global_map, by_chain, chains


def _line_chain_key(line: str, chains) -> str | None:
    plain = _TAGS.sub(" ", line).lower()
    if re.search(r"\bsolana\b", plain):
        return "solana"
    for c in chains:
        slug = str(c.slug or "").lower()
        name = str(c.name or "").lower()
        if slug and re.search(rf"(?<![a-z0-9]){re.escape(slug)}(?![a-z0-9])", plain):
            return slug
        if name and name != slug and name in plain:
            return slug
    return None


def annotate_text(app, text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    try:
        global_map, by_chain, chains = _price_maps(app)
    except Exception:
        return text

    out = []
    for line in text.split("\n"):
        key = _line_chain_key(line, chains)
        local = by_chain.get(key or "", {})

        def repl(match):
            symbol = str(match.group("symbol") or "").upper()
            if symbol in local:
                price = local[symbol]
                known = True
            elif symbol in global_map:
                price = global_map[symbol]
                known = True
            else:
                price = None
                known = False
            if not known:
                return match.group(0)
            tail = line[match.end(): match.end() + 40]
            if "$" in tail or "USD unavailable" in tail:
                return match.group(0)
            rate = match.group("rate") or ""
            base = f"{match.group('amount')} {match.group('symbol')}"
            if price is None or _dec(price) <= 0:
                return f"{base} (USD unavailable){rate}"
            amount = _dec(str(match.group("amount") or "0").replace(",", ""))
            usd = amount * _dec(price)
            return f"{base} (≈ {_fmt_usd(usd)}){rate}"

        out.append(_NUM_SYMBOL.sub(repl, line))
    return "\n".join(out)


def _wrap_text(fn, *, app_index=0):
    def wrapped(*args, **kwargs):
        result = fn(*args, **kwargs)
        app = args[app_index] if len(args) > app_index else kwargs.get("app")
        if app is None:
            return result
        if isinstance(result, tuple) and result and isinstance(result[0], str):
            return (annotate_text(app, result[0]), *result[1:])
        if isinstance(result, str):
            return annotate_text(app, result)
        return result
    wrapped.__name__ = getattr(fn, "__name__", "usd_wrapped")
    wrapped._usd_everywhere_wrapped = True
    return wrapped


def _install_attr(module, name):
    fn = getattr(module, name, None)
    if callable(fn) and not getattr(fn, "_usd_everywhere_wrapped", False):
        wrapped = _wrap_text(fn)
        setattr(module, name, wrapped)
        return wrapped
    return fn


def install():
    ui_names = [
        "chains_page", "wallets_page", "profit_page", "rankings_page", "behaviours_page",
        "copy20_page", "signals_page", "strategies_page", "control_page", "status_page",
        "auto_page", "trading_page", "opportunities_page", "products_page", "power_page",
        "queue_page", "build_report_html", "alerts_page",
    ]
    for name in ui_names:
        _install_attr(_ui, name)

    # The live-reporting module owns the final composed SiBot main/leaders/report
    # functions. Wrap those final functions, then point telegram_sibot_patch at
    # the same wrappers. Never replace them with an older pre-composition layer.
    for name in ["main_page", "leaders_page", "report_text"]:
        wrapped = _install_attr(_live, name)
        if callable(wrapped):
            setattr(_sibotui, name, wrapped)

    # These SiBot pages are not superseded by telegram_live_reporting_patch.
    for name in ["settings_page", "top20_summary_page", "top20_page", "positions_page"]:
        _install_attr(_sibotui, name)

    for name in ["solana_page", "solana_positions_page"]:
        _install_attr(_sollive, name)
    for name in ["solana_top20_page", "solana_leaders_page"]:
        _install_attr(_intel, name)
    _install_attr(_solwallet, "wallet_page")
    _install_attr(_solwallet, "solwallet_page")
    _install_attr(_multi, "wallet_hub_page")
    _install_attr(_multi, "evmwallet_page")

    _install_attr(_dash, "user_dashboard_text")
    _install_attr(_dash, "master_dashboard_text")


install()
