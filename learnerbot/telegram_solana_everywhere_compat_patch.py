from __future__ import annotations

import copy
import html
import time
from decimal import Decimal

import requests

from . import solana_sibot as _sol
from . import telegram_dashboard_patch as _dash
from . import telegram_ui as _ui
from .solana_live_patch import live_enabled
from .solana_wallet_store import SolanaWalletStore
from .user_registry import all_users

_PREV_MENU = _ui.menu_keyboard
_PREV_USER_DASH = _dash.user_dashboard_text
_PREV_MASTER_DASH = _dash.master_dashboard_text
_SOL_PRICE_CACHE = {"ts": 0.0, "usd": None}


def _short(v):
    v = str(v or "")
    return v if len(v) <= 18 else f"{v[:8]}…{v[-6:]}"


def _sol_price_usd():
    now = time.time()
    if now - float(_SOL_PRICE_CACHE.get("ts") or 0) < 60:
        return _SOL_PRICE_CACHE.get("usd")
    usd = None
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
            timeout=8,
            headers={"User-Agent": "BOOT-capital-dashboard-solana/2.3.8"},
        )
        r.raise_for_status()
        value = (r.json().get("solana") or {}).get("usd")
        if value is not None:
            usd = Decimal(str(value))
    except Exception:
        pass
    _SOL_PRICE_CACHE.update({"ts": now, "usd": usd})
    return usd


def _sol_capital(app, tid):
    store = SolanaWalletStore(app.csv_dir, app.data_dir)
    meta = store.get_meta(tid)
    address = str(meta.get("address") or "")
    result = _sol._rpc(app, "getBalance", [address, {"commitment": "confirmed"}]) or {}
    native = Decimal(int(result.get("value") or 0)) / Decimal(1_000_000_000)
    positions = _sol.position_rows(app, tid, open_only=False)
    open_live = [p for p in positions if str(p.get("mode") or "").upper() == "LIVE" and str(p.get("status") or "").upper() == "OPEN"]
    closed_live = [p for p in positions if str(p.get("mode") or "").upper() == "LIVE" and str(p.get("status") or "").upper() == "CLOSED"]
    token_exit = sum((Decimal(str(p.get("current_exit_sol") or 0)) for p in open_live), Decimal(0))
    # If a newly confirmed LIVE position has not had its first monitor quote yet,
    # show its entry cost as a conservative temporary proxy rather than zero.
    for p in open_live:
        if Decimal(str(p.get("current_exit_sol") or 0)) <= 0:
            token_exit += Decimal(str(p.get("entry_cost_sol") or 0))
    realised = sum((Decimal(str(p.get("realised_net_sol") or 0)) for p in closed_live), Decimal(0))
    unrealised = sum((Decimal(str(p.get("unrealised_net_sol") or 0)) for p in open_live), Decimal(0))
    total_sol = native + token_exit
    price = _sol_price_usd()
    return {
        "address": address,
        "native": native,
        "open": len(open_live),
        "token_exit": token_exit,
        "realised": realised,
        "unrealised": unrealised,
        "total_sol": total_sol,
        "usd": (total_sol * price) if price is not None else None,
        "price": price,
        "live": bool(live_enabled(app, tid)),
    }


def menu_keyboard(app=None, chat_id=None):
    kb = copy.deepcopy(_PREV_MENU(app, chat_id))
    replacements = {
        "🤖 SiBot": "🤖 SiBot — EVM + SOL",
        "💰 Capital & P&L": "💰 Capital & P&L — All",
        "📊 My Capital & P&L": "📊 My Capital & P&L — All",
        "🔐 Wallets": "🔐 Wallets — EVM + SOL",
        "💱 Trading": "💱 Trading — All Chains",
        "⚡ Auto Trade": "⚡ Auto Trade — All Chains",
        "🛰 Opportunities": "🛰 Opportunities — All Chains",
        "🧺 Products": "🧺 Products — All Chains",
        "🔥 Full Power": "🔥 Full Power — All Chains",
        "📡 Status": "📡 Status — All Chains",
        "❓ Help": "❓ Help — EVM + SOL",
        "⚙️ Control": "⚙️ Control — All Chains",
        "🌐 Chains": "🌐 Chains — EVM + SOL",
        "💰 Profit Research": "💰 Profit Research — All",
        "🏆 Rankings": "🏆 Rankings — All",
        "👥 Copy Top 20": "👥 Copy Top 20 — EVM + SOL",
        "🚦 IN / OUT": "🚦 Signals — EVM + SOL",
        "🔬 Behaviours": "🔬 Behaviours — EVM + SOL",
        "🧠 Strategies": "🧠 Strategies — All",
        "🤖 Observed Wallets": "🤖 Observed Wallets — All",
        "📥 Queue": "📥 Execution / LIVE State",
        "📊 Full Technical Report": "📊 Full Report — All Chains",
        "🏦 Trading Wallets & Capital": "🏦 Wallets & Capital — All",
    }
    # Also recognise labels produced by the older/base menu before the visual layer.
    replacements.update({
        "💱 My Live Trading": "💱 Trading — All Chains",
        "⚡ My Auto Routes": "⚡ Auto Trade — All Chains",
        "🧺 Auto Products": "🧺 Products — All Chains",
        "💰 Wallet Profit": "💰 Wallet Profit — All",
        "🏆 Highest & Fastest": "🏆 Highest & Fastest — All",
        "🔬 Trade Behaviours": "🔬 Behaviours — EVM + SOL",
        "📥 Execution Queue": "📥 Execution / LIVE State",
        "📊 Full Report": "📊 Full Report — All Chains",
    })
    for row in kb.get("inline_keyboard", []):
        for button in row:
            text = button.get("text")
            if text in replacements:
                button["text"] = replacements[text]
    return kb


def _sol_user_section(app, tid):
    try:
        s = _sol_capital(app, tid)
    except Exception as exc:
        return ["<b>🟣 SOLANA CAPITAL &amp; P&amp;L</b>", f"⚠️ Solana wallet/balance unavailable: <code>{html.escape(type(exc).__name__)}</code>"]
    usd = f"≈ <b>${s['usd']:,.2f}</b>" if s["usd"] is not None else "USD price unavailable"
    return [
        "<b>🟣 SOLANA CAPITAL &amp; P&amp;L</b>",
        f"Active wallet: <code>{html.escape(_short(s['address']))}</code> | LIVE <b>{'ARMED' if s['live'] else 'OFF'}</b>",
        f"Native balance: <b>{s['native']:.9f} SOL</b>",
        f"Open LIVE token positions: <b>{s['open']}</b> | current/entry SOL-equivalent ≈ <b>{s['token_exit']:.9f} SOL</b>",
        f"Estimated Solana capital: <b>{s['total_sol']:.9f} SOL</b> {usd}",
        f"LIVE realised P&amp;L: <b>{s['realised']:+.9f} SOL</b> | open estimated P&amp;L: <b>{s['unrealised']:+.9f} SOL</b>",
        "<i>Solana token-position value uses the latest stored Jupiter exit valuation; a brand-new position temporarily uses entry cost until its first monitor quote.</i>",
    ]


def user_dashboard_text(app, telegram_id):
    base = _PREV_USER_DASH(app, telegram_id)
    return base.rstrip() + "\n\n" + "\n".join(_sol_user_section(app, telegram_id))


def master_dashboard_text(app, master_id):
    base = _PREV_MASTER_DASH(app, master_id)
    lines = ["<b>🟣 SOLANA — PLATFORM CAPITAL</b>"]
    total_sol = Decimal(0)
    total_usd = Decimal(0)
    priced = True
    count = 0
    trading = 0
    for u in all_users(app.csv_dir):
        tid = str(u.get("telegram_id") or "")
        if not tid:
            continue
        try:
            s = _sol_capital(app, tid)
        except Exception:
            continue
        count += 1
        total_sol += s["total_sol"]
        if s["usd"] is None:
            priced = False
        else:
            total_usd += s["usd"]
        if s["live"]:
            trading += 1
        lines.append(
            f"• <code>{html.escape(tid)}</code> <code>{html.escape(_short(s['address']))}</code> — "
            f"{'🟢 LIVE' if s['live'] else '⚪ OFF'} | capital ≈ <b>{s['total_sol']:.6f} SOL</b> | open {s['open']}"
        )
    if not count:
        lines.append("No configured Solana wallets found.")
    else:
        usd = f"≈ <b>${total_usd:,.2f}</b>" if priced else "(USD total incomplete)"
        lines += [
            f"Solana wallets: <b>{count}</b> | LIVE armed: <b>{trading}</b>",
            f"Total estimated Solana capital: <b>{total_sol:.6f} SOL</b> {usd}",
        ]
    return base.rstrip() + "\n\n" + "\n".join(lines)


def install():
    _ui.menu_keyboard = menu_keyboard
    _dash.menu_keyboard = menu_keyboard
    _dash.user_dashboard_text = user_dashboard_text
    _dash.master_dashboard_text = master_dashboard_text


install()
