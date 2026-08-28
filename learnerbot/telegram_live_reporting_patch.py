from __future__ import annotations

import copy
import html
from decimal import Decimal

from . import hourly_capital_alert_patch as _hourly
from . import sibot as _sibot
from . import solana_sibot as _sol
from . import telegram_sibot_intelligence_patch as _intel
from . import telegram_sibot_patch as _tg
from . import telegram_solana_everywhere_compat_patch as _compat
from .config import load_chains
from .solana_live_patch import live_enabled
from .solana_wallet_store import SolanaWalletStore

_PREV_LEADERS_PAGE = _tg.leaders_page
_PREV_REPORT_TEXT = _tg.report_text
_PREV_MAIN_PAGE = _tg.main_page
_PREV_SIBOT_KEYBOARD = _tg.sibot_keyboard
_PREV_HELP_PAGE = _tg.help_page

DIV = "━━━━━━━━━━━━"


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _dec(v, default="0"):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(str(default))


def _short(v):
    v = str(v or "")
    return v if len(v) <= 18 else f"{v[:8]}…{v[-6:]}"


def _fmt_usd(v):
    return f"${_dec(v):,.2f}"


def _fmt_native(v):
    d = _dec(v)
    if abs(d) >= 1:
        return f"{d:.5f}".rstrip("0").rstrip(".")
    return f"{d:.9f}".rstrip("0").rstrip(".") or "0"


def _sol_snapshot(app, tid):
    s = _compat._sol_capital(app, tid)
    signing = False
    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        meta = store.get_meta(tid)
        signing = store.has_private_key(tid, meta.get("wallet_id"))
    except Exception:
        pass
    s["signing"] = signing
    s["leaders"] = _sol.leader_rows(app, tid)
    s["rankings"] = _sol.ranking_rows(app, tid)
    s["cfg"] = _sol.settings(app)
    return s


def _evm_sibot_state(app, tid, chain):
    cfg = _sibot.user_settings(app, tid, chain.chain_id)
    enabled = _bool(cfg.get("enabled"), False)
    auto = _bool(cfg.get("auto_trade_enabled"), False)
    if auto:
        try:
            ok, reason = _sibot._gate_live(app, tid, chain)
        except Exception as exc:
            ok, reason = False, str(exc)
        if ok:
            return "🟢 LIVE AUTO", ""
        return "🟠 LIVE REQUESTED — BLOCKED", str(reason or "gate failed")
    if enabled:
        return "🧪 SHADOW", ""
    return "⚪ OFF", ""


def build_hourly_capital_alert(app, tid) -> str:
    """All-chain hourly capital/gas report, including real Solana LIVE state."""
    data = _hourly.user_dashboard_data(app, tid)
    wallets = list(data.get("wallets") or [])
    active = next((w for w in wallets if _bool(w.get("active"), False)), wallets[0] if wallets else None)
    evm_total = _dec(data.get("capital_usd"), 0)

    try:
        sol = _sol_snapshot(app, tid)
    except Exception:
        sol = None

    total = evm_total + (_dec(sol.get("usd"), 0) if sol and sol.get("usd") is not None else Decimal(0))
    lines = [
        "<b>⏰ Hourly Capital &amp; Gas Check — ALL CHAINS</b>",
        DIV,
        f"💰 Total priced capital  <b>{_fmt_usd(total)}</b>",
    ]
    if sol and sol.get("usd") is None:
        lines.append(f"🟣 Plus Solana capital  <b>{_fmt_native(sol.get('total_sol'))} SOL</b> (USD price unavailable)")

    if active:
        lines.append(f"👛 Active EVM wallet  <code>{html.escape(_short(active.get('address')))}</code>")
    else:
        lines.append("👛 Active EVM wallet  <b>not configured</b>")

    chain_map = {c.chain_id: c for c in _hourly.load_chains(app, enabled_only=True)}
    snaps = {int(s.get("chain_id")): s for s in (active.get("chains", []) if active else [])}
    warnings = []
    opportunity_without_capital = []

    for cid, chain in chain_map.items():
        snap = snaps.get(int(cid), {})
        native = _dec(snap.get("native_balance"), 0)
        capital = _dec(snap.get("capital_usd"), 0)
        reserve = _hourly._reserve_for(app, tid, chain)
        min_trade = _hourly._min_trade_for(app, tid, chain)
        usable_native = max(Decimal(0), native - reserve)
        research = _sibot.ranking_rows(app, tid, chain.chain_id)
        profitable_wallets = len(research)
        sibot_state, gate_reason = _evm_sibot_state(app, tid, chain)
        wallet_state = str(snap.get("trading_state") or "OFF").upper()

        if native < reserve:
            gas = "🔴 GAS BELOW RESERVE"
            warnings.append(f"{chain.name}: {native:f} {chain.native_symbol} < reserve {reserve:f}")
        elif reserve > 0 and native < reserve * Decimal("1.5"):
            gas = "🟠 GAS LOW"
            warnings.append(f"{chain.name}: gas balance is close to the configured reserve")
        else:
            gas = "🟢 gas OK"

        lines += [
            "",
            f"<b>🌐 {html.escape(chain.name)}</b>",
            f"🤖 SiBot  <b>{html.escape(sibot_state)}</b>",
            f"💱 Wallet trading state  <b>{html.escape(wallet_state)}</b>",
            f"💵 Capital  <b>{_fmt_usd(capital)}</b>",
            f"⛽ Native  <b>{_fmt_native(native)} {html.escape(chain.native_symbol)}</b>  •  {gas}",
            f"📤 Usable after gas reserve  <b>{_fmt_native(usable_native)} {html.escape(chain.native_symbol)}</b>",
            f"🏆 Positive-profit wallets found  <b>{profitable_wallets}</b>",
        ]
        if gate_reason:
            lines.append(f"🔒 LIVE gate: <code>{html.escape(gate_reason[:180])}</code>")
        if profitable_wallets > 0 and usable_native < min_trade:
            opportunity_without_capital.append(
                f"{chain.name}: {profitable_wallets} positive-profit wallet(s), but usable native capital is below {min_trade:f} {chain.native_symbol}"
            )

    lines += ["", "<b>🟣 Solana</b>"]
    if not sol:
        lines += [
            "👛 Active Solana wallet  <b>not configured / unavailable</b>",
            "🤖 Solana SiBot  <b>🔴 LIVE OFF</b>",
        ]
    else:
        cfg = sol.get("cfg") or {}
        reserve = max(Decimal("0.01"), _dec(cfg.get("live_min_sol_reserve"), ".02"))
        # Fixed LIVE Solana trade size (see solana_live_patch.live_limits).
        trade = Decimal("0.009")
        native = _dec(sol.get("native"), 0)
        usable = max(Decimal(0), native - reserve)
        sol_live = bool(sol.get("live"))
        signing = bool(sol.get("signing"))
        if sol_live and signing:
            state = "🟢 LIVE AUTO ARMED"
        elif sol_live:
            state = "🟠 LIVE ON — SIGNER NOT READY"
        else:
            state = "🔴 LIVE OFF"
        gas = "🟢 SOL reserve OK" if native >= reserve + trade else "🔴 SOL BELOW LIVE TRADE + RESERVE"
        if native < reserve + trade:
            warnings.append(f"Solana: {native:f} SOL < live trade {trade:f} + reserve {reserve:f}")
        usd = _fmt_usd(sol.get("usd")) if sol.get("usd") is not None else "USD price unavailable"
        lines += [
            f"👛 Active Solana wallet  <code>{html.escape(_short(sol.get('address')))}</code>  •  <b>{'SIGNING READY' if signing else 'PUBLIC ONLY'}</b>",
            f"🤖 Solana SiBot  <b>{state}</b>",
            f"💵 Estimated capital  <b>{_fmt_native(sol.get('total_sol'))} SOL</b>  •  {usd}",
            f"⛽ Native  <b>{_fmt_native(native)} SOL</b>  •  {gas}",
            f"📤 Usable after reserve  <b>{_fmt_native(usable)} SOL</b>",
            f"💼 Open LIVE positions  <b>{int(sol.get('open') or 0)}</b>",
            f"🏆 Positive-profit wallets found  <b>{len(sol.get('rankings') or [])}</b>  •  selected leaders <b>{len(sol.get('leaders') or [])}</b>",
        ]

    if warnings:
        lines += ["", "<b>⚠️ GAS / CAPITAL WARNINGS</b>"]
        lines.extend(f"▫️ {html.escape(x)}" for x in warnings[:10])
    if opportunity_without_capital:
        lines += ["", "<b>🚨 PROFIT EVIDENCE BUT NO USABLE CAPITAL</b>"]
        lines.extend(f"▫️ {html.escape(x)}" for x in opportunity_without_capital[:8])

    lines += [
        "",
        "<i>LIVE status is read from the actual per-user/per-chain trading gates. Solana uses its separate Solana LIVE switch and signing wallet; no new Solana SHADOW entries are created.</i>",
    ]
    return "\n".join(lines)


def sibot_keyboard(app, tid):
    kb = copy.deepcopy(_PREV_SIBOT_KEYBOARD(app, tid))
    cfg = _sibot.user_settings(app, tid, 0)
    evm_auto = _bool(cfg.get("auto_trade_enabled"), False)
    try:
        sol_live = bool(live_enabled(app, tid))
    except Exception:
        sol_live = False
    for row in kb.get("inline_keyboard", []):
        for b in row:
            cb = str(b.get("callback_data") or "")
            if cb.startswith("sibot:auto:"):
                b["text"] = "🔴 EVM LIVE AUTO" if evm_auto else "🧪 EVM SHADOW"
            elif cb == "sibot:solana":
                b["text"] = "🟣 Solana LIVE" if sol_live else "🟣 Solana OFF"
    return kb


def main_page(app, tid):
    text = _PREV_MAIN_PAGE(app, tid)
    text = text.replace("🔴 LIVE AUTO", "🔴 EVM LIVE AUTO").replace("🧪 SHADOW", "🧪 EVM SHADOW")
    try:
        sol = _sol_snapshot(app, tid)
        sol_state = "🟢 LIVE AUTO ARMED" if sol.get("live") and sol.get("signing") else "🟠 LIVE ON — signer missing" if sol.get("live") else "🔴 LIVE OFF"
        extra = [
            "",
            "<b>🟣 SOLANA LIVE STATE</b>",
            f"{sol_state}",
            f"👛 <code>{html.escape(_short(sol.get('address')))}</code>  •  balance <b>{_fmt_native(sol.get('native'))} SOL</b>",
            f"🏆 Leaders <b>{len(sol.get('leaders') or [])}</b>  •  open LIVE positions <b>{int(sol.get('open') or 0)}</b>",
        ]
        return text.rstrip() + "\n" + "\n".join(extra)
    except Exception:
        return text.rstrip() + "\n\n🟣 Solana LIVE state: <b>unavailable</b>"


def leaders_page(app, tid, chain=None):
    key = str(chain or "").strip().lower()
    if key in {"solana", "sol", str(_sol.SOLANA_CHAIN_ID)}:
        return _intel.solana_leaders_page(app, tid)
    base = _PREV_LEADERS_PAGE(app, tid, chain)
    if chain is not None:
        return base
    rows = _sol.leader_rows(app, tid)
    lines = ["", "<b>🟣 Solana</b>"]
    if not rows:
        lines.append("⏳ No qualified Solana leaders yet.")
    else:
        for r in rows:
            rank = int(r.get("rank") or 0)
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🏅"
            lines.append(f"{medal} <b>#{rank}</b> <code>{html.escape(_short(r.get('wallet')))}</code>")
            lines.append(f"   💰 {_dec(r.get('net_profit_sol')):+.6f} SOL  •  🎯 {float(r.get('win_rate') or 0):.1f}%  •  🔁 {int(r.get('closed_trades') or 0)}")
    return base.rstrip() + "\n" + "\n".join(lines)


def _all_live_state_lines(app, tid):
    lines = ["<b>🚦 LIVE STATE — ALL CHAINS</b>"]
    for c in load_chains(app, enabled_only=True):
        state, reason = _evm_sibot_state(app, tid, c)
        suffix = f" — {html.escape(reason[:120])}" if reason else ""
        lines.append(f"• {html.escape(c.name)}: <b>{html.escape(state)}</b>{suffix}")
    try:
        sol = _sol_snapshot(app, tid)
        state = "🟢 LIVE AUTO ARMED" if sol.get("live") and sol.get("signing") else "🟠 LIVE ON — signer missing" if sol.get("live") else "🔴 LIVE OFF"
        lines.append(f"• Solana: <b>{state}</b>  •  <code>{html.escape(_short(sol.get('address')))}</code>")
    except Exception:
        lines.append("• Solana: <b>unavailable</b>")
    return lines


def _combined_leader_lines(app, tid):
    lines = ["<b>🏆 SELECTED LEADERS — EVM + SOLANA</b>"]
    evm = _sibot.leader_rows(app, tid)
    by_chain = {}
    for r in evm:
        by_chain.setdefault(int(r.get("chain_id") or 0), []).append(r)
    chain_map = {c.chain_id: c for c in load_chains(app, enabled_only=False)}
    for cid, rows in sorted(by_chain.items()):
        c = chain_map.get(cid)
        name = c.name if c else str(cid)
        vals = ", ".join(f"#{int(r.get('rank') or 0)} {_short(r.get('wallet'))}" for r in rows)
        lines.append(f"• {html.escape(name)}: <code>{html.escape(vals)}</code>")
    sol = _sol.leader_rows(app, tid)
    if sol:
        vals = ", ".join(f"#{int(r.get('rank') or 0)} {_short(r.get('wallet'))}" for r in sol)
        lines.append(f"• Solana: <code>{html.escape(vals)}</code>")
    else:
        lines.append("• Solana: no qualified leader yet")
    return lines


def report_text(app, tid):
    base = _PREV_REPORT_TEXT(app, tid)
    try:
        sol_section = _compat._sol_user_section(app, tid)
    except Exception:
        sol_section = ["<b>🟣 SOLANA CAPITAL &amp; P&amp;L</b>", "Solana capital unavailable."]
    lines = [""] + sol_section + [""] + _all_live_state_lines(app, tid) + [""] + _combined_leader_lines(app, tid)
    return base.rstrip() + "\n" + "\n".join(lines)


def help_page():
    text = _PREV_HELP_PAGE()
    old = "• Solana is currently analysis + SHADOW only; LIVE Solana signing is intentionally disabled."
    new = "• Solana has a separate guarded LIVE AUTO path using the active Solana signing wallet, Jupiter validation/simulation, and the same leader-follow exit protections."
    return text.replace(old, new).replace("💼 SHADOW Positions", "💼 LIVE Positions")


def solana_context_keyboard():
    return {"inline_keyboard": [
        [{"text": "📈 Top 20", "callback_data": "sibot:solana:top20"}, {"text": "🏆 Leaders", "callback_data": "sibot:solana:leaders"}],
        [{"text": "💼 LIVE Positions", "callback_data": "sibot:solana:positions"}, {"text": "🚀 LIVE Controls", "callback_data": "sibot:solana"}],
        [{"text": "🔄 Refresh", "callback_data": "sibot:solana:refresh"}],
        [{"text": "⬅️ SiBot", "callback_data": "menu:sibot"}],
    ]}


def install():
    _hourly.build_hourly_capital_alert = build_hourly_capital_alert
    _tg.sibot_keyboard = sibot_keyboard
    _tg.main_page = main_page
    _tg.leaders_page = leaders_page
    _tg.report_text = report_text
    _tg.help_page = help_page
    _intel.solana_keyboard = solana_context_keyboard


install()
