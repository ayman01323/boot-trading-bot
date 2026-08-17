from __future__ import annotations

import html
import threading
from decimal import Decimal

from . import telegram as _tg
from . import telegram_ui as _ui
from . import sibot as _sibot
from .capital_dashboard import user_dashboard_data
from .config import load_chains
from .multi_wallet_store import MultiWalletStore
from .operator_control import parse_float
from .sibot import (
    SETTING_SPECS,
    can_start_live,
    leader_rows,
    performance,
    position_rows,
    ranking_rows,
    refresh_all_rankings,
    request_history_refresh,
    set_user_value,
    setting_value,
    start_workers,
    user_settings,
)
from .user_registry import require_user

_original_menu_keyboard = _ui.menu_keyboard
_original_handle_update = _ui.handle_update
_original_start_menu_thread = _ui.start_menu_thread
_original_set_commands = _ui.set_commands
_original_sibot_notify = _sibot._notify
_PENDING = {}
_BUSY = set()
_LOCK = threading.Lock()
DIV = "━━━━━━━━━━━━━━━━━━━━"


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _short(a):
    a = str(a or "")
    return a if len(a) <= 18 else f"{a[:8]}…{a[-6:]}"


def _chain(app, slug_or_id):
    key = str(slug_or_id or "").strip().lower()
    for c in load_chains(app, enabled_only=False):
        if c.slug == key or str(c.chain_id) == key:
            return c
    return None


def _active_wallet(app, tid):
    try:
        return MultiWalletStore(app.data_dir, app.csv_dir).get_meta(tid)
    except Exception:
        return None


def _fmt_native(v, digits=6):
    try:
        d = Decimal(str(v or 0))
    except Exception:
        d = Decimal(0)
    return f"{d:+.{digits}f}" if d else "0"


def _fmt_usd(v):
    try:
        return f"${Decimal(str(v or 0)):,.2f}"
    except Exception:
        return "$0.00"


def _pnl_icon(v):
    try:
        d = Decimal(str(v or 0))
    except Exception:
        d = Decimal(0)
    return "🟢" if d > 0 else "🔴" if d < 0 else "⚪"


def _safe_name(c):
    return html.escape(c.name if c else "Unknown")


def _edit_message(app, chat_id, message_id, text, keyboard):
    payload = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
        "reply_markup": keyboard,
    }
    try:
        _tg._json("editMessageText", app.telegram_bot_token, payload=payload, timeout=20)
        return True
    except Exception:
        return False


def _render(app, tid, text, keyboard, cb=None):
    message_id = ((cb or {}).get("message") or {}).get("message_id")
    if message_id and _edit_message(app, tid, message_id, text, keyboard):
        return
    _ui._send(app, tid, text, keyboard)


def _sibot_notify_clean(app, tid, text):
    clean = str(text or "").replace("SiMo", "SiBot").replace("SIMO", "SIBOT")
    return _original_sibot_notify(app, tid, clean)


def set_commands(token: str):
    """Keep the existing command list and append the compact SiBot commands."""
    _original_set_commands(token)
    try:
        commands = _tg._json("getMyCommands", token, payload={}, timeout=15) or []
        existing = {str(x.get("command") or "") for x in commands}
        extras = [
            {"command": "sibot", "description": "Open SiBot dashboard"},
            {"command": "sibotstart", "description": "Start SiBot LIVE AUTO: CONFIRM"},
            {"command": "sibotstop", "description": "Stop new SiBot entries"},
            {"command": "sibotleaders", "description": "Show SiBot leaders"},
            {"command": "sibottop20", "description": "Show SiBot Top-20 by chain"},
            {"command": "sibotpositions", "description": "Show SiBot open positions"},
            {"command": "sibotreport", "description": "Wallet capital and SiBot P&L"},
            {"command": "sibotsettings", "description": "Open SiBot settings"},
        ]
        commands.extend(x for x in extras if x["command"] not in existing)
        _tg._json("setMyCommands", token, payload={"commands": commands[:100]}, timeout=15)
    except Exception as exc:
        print("[sibot-commands]", type(exc).__name__, exc)


def menu_keyboard(app=None, chat_id=None):
    kb = _original_menu_keyboard(app, chat_id)
    rows = list(kb.get("inline_keyboard") or [])
    row = [{"text": "🤖 SiBot", "callback_data": "menu:sibot"}]
    if not any(any(b.get("callback_data") == "menu:sibot" for b in r) for r in rows):
        insert_at = 1 if rows else 0
        for i, r in enumerate(rows):
            if any(b.get("callback_data") == "menu:capital" for b in r):
                insert_at = i + 1
                break
        rows.insert(insert_at, row)
    return {"inline_keyboard": rows}


def sibot_keyboard(app, tid):
    cfg = user_settings(app, tid, 0)
    enabled = _bool(cfg.get("enabled"), False)
    auto = _bool(cfg.get("auto_trade_enabled"), False)
    return {"inline_keyboard": [
        [
            {"text": f"{'🟢 RUNNING' if enabled else '⚪ STOPPED'}", "callback_data": "sibot:strategy:off" if enabled else "sibot:strategy:on"},
            {"text": f"{'🔴 LIVE AUTO' if auto else '🧪 SHADOW'}", "callback_data": "sibot:auto:off" if auto else "sibot:auto:arm"},
        ],
        [
            {"text": "🏆 Leaders", "callback_data": "sibot:leaders"},
            {"text": "📈 Top 20", "callback_data": "sibot:top20"},
        ],
        [
            {"text": "💼 Positions", "callback_data": "sibot:positions"},
            {"text": "💰 Capital & P&L", "callback_data": "sibot:report"},
        ],
        [
            {"text": "⚙️ Settings", "callback_data": "sibot:settings"},
            {"text": "❓ Help", "callback_data": "sibot:help"},
        ],
        [
            {"text": "🔄 Refresh", "callback_data": "sibot:refresh"},
            {"text": "⬅️ Main Menu", "callback_data": "menu:home"},
        ],
    ]}


def _chain_picker(app, prefix, back="menu:sibot"):
    buttons = []
    for c in load_chains(app, enabled_only=True):
        buttons.append({"text": c.slug.upper(), "callback_data": f"{prefix}:{c.chain_id}"})
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([{"text": "⬅️ SiBot", "callback_data": back}])
    return {"inline_keyboard": rows}


def settings_keyboard(app, tid):
    c = user_settings(app, tid, 0)
    partial = _bool(c.get("mirror_partial_sells"), True)
    return {"inline_keyboard": [
        [
            {"text": f"🗓 History {c.get('lookback_days','60')}d", "callback_data": "sibot:set:lookback_days"},
            {"text": f"🏆 Leaders {c.get('leaders_per_chain','2')}", "callback_data": "sibot:set:leaders_per_chain"},
        ],
        [
            {"text": f"💵 Buy {c.get('allocation_pct','20')}%", "callback_data": "sibot:set:allocation_pct"},
            {"text": f"🧱 Exposure {c.get('max_exposure_pct','60')}%", "callback_data": "sibot:set:max_exposure_pct"},
        ],
        [
            {"text": f"✅ Trades {c.get('min_closed_trades','50')}+", "callback_data": "sibot:set:min_closed_trades"},
            {"text": f"🎯 Win {c.get('min_win_rate_pct','55')}%+", "callback_data": "sibot:set:min_win_rate_pct"},
        ],
        [
            {"text": f"⏱ Signal {c.get('max_signal_age_seconds','20')}s", "callback_data": "sibot:set:max_signal_age_seconds"},
            {"text": f"📉 Entry +{c.get('max_entry_deterioration_pct','1.5')}%", "callback_data": "sibot:set:max_entry_deterioration_pct"},
        ],
        [
            {"text": f"🛑 Stop {c.get('stop_loss_pct','10')}%", "callback_data": "sibot:set:stop_loss_pct"},
            {"text": f"🎯 Take {c.get('take_profit_pct','25')}%", "callback_data": "sibot:set:take_profit_pct"},
        ],
        [
            {"text": f"📦 Max {c.get('max_positions_per_chain','5')} positions", "callback_data": "sibot:set:max_positions_per_chain"},
            {"text": f"🕒 Hold {c.get('max_hold_hours','24')}h", "callback_data": "sibot:set:max_hold_hours"},
        ],
        [{"text": f"{'✅' if partial else '❌'} Follow partial sells", "callback_data": "sibot:partial:toggle"}],
        [{"text": "⬅️ SiBot", "callback_data": "menu:sibot"}],
    ]}


def main_page(app, tid):
    cfg = user_settings(app, tid, 0)
    enabled = _bool(cfg.get("enabled"), False)
    auto = _bool(cfg.get("auto_trade_enabled"), False)
    leaders = leader_rows(app, tid)
    positions = position_rows(app, tid, open_only=True)
    wallet = _active_wallet(app, tid)
    wallet_text = _short((wallet or {}).get("address") or "not configured")
    mode = "🔴 LIVE AUTO" if auto else "🧪 SHADOW"
    state = "🟢 RUNNING" if enabled else "⚪ STOPPED"
    return "\n".join([
        "<b>🤖 SiBot</b>",
        DIV,
        f"{state}    {mode}",
        f"👛 <code>{html.escape(wallet_text)}</code>",
        "",
        "<b>📌 CURRENT SETUP</b>",
        f"🗓 {html.escape(str(cfg.get('lookback_days','60')))}d history   •   🏆 {html.escape(str(cfg.get('leaders_per_chain','2')))} leaders/chain",
        f"💵 {html.escape(str(cfg.get('allocation_pct','20')))}% per entry   •   🧱 {html.escape(str(cfg.get('max_exposure_pct','60')))}% max exposure",
        "",
        "<b>📊 NOW</b>",
        f"🏆 Leaders selected: <b>{len(leaders)}</b>",
        f"💼 Open positions: <b>{len(positions)}</b>",
        "",
        "Tap a button below for details.",
    ])


def settings_page(app, tid):
    c = user_settings(app, tid, 0)
    return "\n".join([
        "<b>⚙️ SiBot Settings</b>",
        DIV,
        "<b>🎯 POSITION SIZE</b>",
        f"💵 Per entry  <b>{c.get('allocation_pct','20')}%</b>",
        f"🧱 Max exposure  <b>{c.get('max_exposure_pct','60')}%</b>",
        f"📦 Max positions  <b>{c.get('max_positions_per_chain','5')}/chain</b>",
        "",
        "<b>🏆 LEADER FILTER</b>",
        f"🗓 History  <b>{c.get('lookback_days','60')} days</b>",
        f"🏆 Leaders  <b>{c.get('leaders_per_chain','2')}/chain</b>",
        f"✅ Closed trades  <b>{c.get('min_closed_trades','50')}+</b>",
        f"🎯 Win rate  <b>{c.get('min_win_rate_pct','55')}%+</b>",
        "",
        "<b>🛡 PROTECTION</b>",
        f"⏱ Signal age  <b>≤ {c.get('max_signal_age_seconds','20')}s</b>",
        f"📉 Worse entry  <b>≤ {c.get('max_entry_deterioration_pct','1.5')}%</b>",
        f"🛑 Stop loss  <b>{c.get('stop_loss_pct','10')}%</b>",
        f"🎯 Take profit  <b>{c.get('take_profit_pct','25')}%</b>",
        f"🕒 Max hold  <b>{c.get('max_hold_hours','24')}h</b>",
        "",
        "Tap a setting button to change it.",
    ])


def leaders_page(app, tid, chain=None):
    target = _chain(app, chain) if chain else None
    rows = leader_rows(app, tid, target.chain_id if target else None)
    title = f"🏆 SiBot Leaders — {_safe_name(target)}" if target else "🏆 SiBot Leaders"
    L = [f"<b>{title}</b>", DIV]
    if not rows:
        return "\n".join(L + ["", "⏳ No qualified leaders yet.", "History may still be building."])
    last_chain = None
    for r in rows:
        c = _chain(app, r["chain_id"])
        if r["chain_id"] != last_chain:
            L += ["", f"<b>🌐 {_safe_name(c)}</b>"]
            last_chain = r["chain_id"]
        sym = c.native_symbol if c else "native"
        medal = "🥇" if int(r["rank"]) == 1 else "🥈" if int(r["rank"]) == 2 else "🏅"
        L.append(f"{medal} <b>#{r['rank']}</b> <code>{html.escape(_short(r['wallet']))}</code>")
        L.append(f"   💰 {Decimal(str(r['net_profit_native'])):+.6f} {html.escape(sym)}   •   🎯 {float(r['win_rate']):.1f}%   •   🔁 {r['closed_trades']}")
    return "\n".join(L)


def top20_summary_page(app, tid):
    rows = ranking_rows(app, tid)
    counts = {}
    for r in rows:
        counts[int(r["chain_id"])] = counts.get(int(r["chain_id"]), 0) + 1
    L = ["<b>📈 SiBot Top 20</b>", DIV, "Choose a chain to keep the report clean:", ""]
    for c in load_chains(app, enabled_only=True):
        L.append(f"🌐 <b>{html.escape(c.name)}</b>  •  {counts.get(c.chain_id,0)} qualified")
    return "\n".join(L)


def top20_page(app, tid, chain):
    target = _chain(app, chain)
    if not target:
        return "<b>📈 SiBot Top 20</b>\nUnknown chain."
    rows = ranking_rows(app, tid, target.chain_id)
    L = [f"<b>📈 SiBot Top 20 — {html.escape(target.name)}</b>", DIV]
    if not rows:
        return "\n".join(L + ["", "⏳ No qualified wallets yet."])
    for r in rows[:20]:
        mark = "✅" if int(r.get("history_complete") or 0) else "⚠️"
        L.append(f"{mark} <b>#{r['rank']}</b> <code>{html.escape(_short(r['wallet']))}</code>")
        L.append(f"   💰 {Decimal(str(r['net_profit_native'])):+.6f} {html.escape(target.native_symbol)}   •   🎯 {float(r['win_rate']):.1f}%   •   🔁 {r['closed_trades']}")
    return "\n".join(L)


def positions_page(app, tid):
    rows = position_rows(app, tid, open_only=True)
    L = ["<b>💼 SiBot Positions</b>", DIV]
    if not rows:
        return "\n".join(L + ["", "✅ No open SiBot positions."])
    for p in rows[:20]:
        c = _chain(app, p["chain_id"])
        sym = c.native_symbol if c else "native"
        pnl = Decimal(str(p.get("unrealised_net_native") or 0))
        pct = float(p.get("unrealised_pct") or 0)
        status = "⏳ EXIT PENDING" if int(p.get("leader_exit_pending") or 0) else "📡 MONITORING"
        L += [
            "",
            f"<b>{html.escape(p.get('symbol') or _short(p['token']))}</b>  •  {_safe_name(c)}  •  <b>{html.escape(p['mode'])}</b>",
            f"{_pnl_icon(pnl)} P&L <b>{pnl:+.6f} {html.escape(sym)}</b>  ({pct:+.2f}%)",
            f"💵 Entry {Decimal(str(p['entry_input_native'])):.6f} {html.escape(sym)}  •  {status}",
        ]
    return "\n".join(L)


def help_page():
    return "\n".join([
        "<b>❓ SiBot — How it works</b>",
        DIV,
        "1️⃣ <b>Rank</b> — checks realised BUY→SELL history and keeps the strongest profitable wallets.",
        "",
        "2️⃣ <b>Select</b> — chooses your configured number of SiBot leaders on each chain.",
        "",
        "3️⃣ <b>Watch</b> — monitors fresh confirmed leader BUY/SELL activity.",
        "",
        "4️⃣ <b>Validate</b> — re-quotes your trade, checks signal age, entry price, sellability, product policy and limits.",
        "",
        "5️⃣ <b>Trade</b> — SHADOW records a simulated position; LIVE AUTO signs only when every safety gate passes.",
        "",
        "6️⃣ <b>Exit</b> — follows profitable leader exits and also enforces your stop-loss/take-profit rules.",
        "",
        "<b>LIVE start</b>  <code>/live on CONFIRM</code> → <code>/sibotstart CONFIRM</code>",
        "<b>Stop new entries</b>  <code>/sibotstop</code>",
        "<b>Shadow</b>  <code>/sibot on</code>",
    ])


def report_text(app, tid):
    d = user_dashboard_data(app, tid)
    sp = performance(app, tid)
    active = next((w for w in d["wallets"] if _bool(w.get("active"), False)), d["wallets"][0] if d["wallets"] else None)
    L = ["<b>💰 SiBot Capital & P&L</b>", DIV]
    if active:
        L += [f"👛 <code>{html.escape(_short(active.get('address')))}</code>", ""]
    total_capital = Decimal(0)
    total_existing = Decimal(0)
    total_sibot_real = Decimal(0)
    total_sibot_unreal = Decimal(0)
    existing_by_slug = d["performance"].get("by_chain", {})
    for c in load_chains(app, enabled_only=True):
        cap = Decimal(0)
        if active:
            snap = next((x for x in active.get("chains", []) if int(x["chain_id"]) == int(c.chain_id)), None)
            if snap:
                cap = Decimal(str(snap.get("capital_usd") or 0))
        native_price = Decimal(str(d.get("native_prices", {}).get(c.slug) or 0))
        existing_native = Decimal(str((existing_by_slug.get(c.slug) or {}).get("net") or 0))
        srow = sp["by_chain"].get(c.chain_id, {})
        sreal = Decimal(str(srow.get("realised") or 0))
        sunreal = Decimal(str(srow.get("unrealised") or 0))
        existing_usd = existing_native * native_price
        sreal_usd = sreal * native_price
        sunreal_usd = sunreal * native_price
        total_capital += cap
        total_existing += existing_usd
        total_sibot_real += sreal_usd
        total_sibot_unreal += sunreal_usd
        chain_total = existing_usd + sreal_usd + sunreal_usd
        L += [
            f"<b>🌐 {html.escape(c.name)}</b>",
            f"💰 Capital  <b>{_fmt_usd(cap)}</b>",
            f"{_pnl_icon(existing_usd)} Other bot realised  <b>{_fmt_usd(existing_usd)}</b>",
            f"{_pnl_icon(sreal_usd)} SiBot realised  <b>{_fmt_usd(sreal_usd)}</b>",
            f"{_pnl_icon(sunreal_usd)} SiBot open  <b>{_fmt_usd(sunreal_usd)}</b>",
            f"📊 Chain P&L  <b>{_fmt_usd(chain_total)}</b>",
            "",
        ]
    combined_real = total_existing + total_sibot_real
    total_pnl = combined_real + total_sibot_unreal
    L += [
        "<b>🌍 OVERALL</b>",
        f"💰 Wallet capital  <b>{_fmt_usd(total_capital)}</b>",
        f"{_pnl_icon(combined_real)} Realised P&L  <b>{_fmt_usd(combined_real)}</b>",
        f"{_pnl_icon(total_sibot_unreal)} Open SiBot P&L  <b>{_fmt_usd(total_sibot_unreal)}</b>",
        f"{_pnl_icon(total_pnl)} Total P&L  <b>{_fmt_usd(total_pnl)}</b>",
        "",
        "<i>Capital uses current priced wallet assets. Realised P&L comes from recorded successful bot executions.</i>",
    ]
    return "\n".join(L)


def _send_report_worker(app, tid, key):
    try:
        _ui._send(app, tid, report_text(app, tid), sibot_keyboard(app, tid))
    except Exception as exc:
        _ui._send(app, tid, f"❌ SiBot report failed\n<code>{html.escape(str(exc)[:260])}</code>", sibot_keyboard(app, tid))
    finally:
        with _LOCK:
            _BUSY.discard(key)


def _refresh_worker(app, tid, key):
    try:
        request_history_refresh(app, tid)
        refresh_all_rankings(app, tid)
        _ui._send(app, tid, "✅ <b>SiBot refresh queued</b>\nRankings were rebuilt from stored history. Historical backfill continues automatically.", sibot_keyboard(app, tid))
    except Exception as exc:
        _ui._send(app, tid, f"❌ Refresh failed\n<code>{html.escape(str(exc)[:260])}</code>", sibot_keyboard(app, tid))
    finally:
        with _LOCK:
            _BUSY.discard(key)


def _start_async(app, tid, kind):
    key = (str(tid), kind)
    with _LOCK:
        if key in _BUSY:
            return False
        _BUSY.add(key)
    if kind == "report":
        target = _send_report_worker
        msg = "⏳ <b>SiBot</b> reading wallet balances and P&L…"
    else:
        target = _refresh_worker
        msg = "⏳ <b>SiBot</b> refreshing rankings…"
    _ui._send(app, tid, msg)
    threading.Thread(target=target, args=(app, tid, key), daemon=True, name=f"sibot-{kind}-{tid}").start()
    return True


def _set_from_text(app, tid, key, raw):
    if key not in SETTING_SPECS:
        raise ValueError("Unknown SiBot setting")
    lo, hi, unit = SETTING_SPECS[key]
    v = parse_float(raw, minimum=lo, maximum=hi, name=f"SiBot {key}")
    if key in {"lookback_days", "leaders_per_chain", "min_closed_trades", "max_signal_age_seconds", "max_positions_per_chain", "max_hold_hours"}:
        value = str(int(v))
    else:
        value = f"{v:g}"
    set_user_value(app, tid, key, value)
    return value, unit


def _handle_pending(app, tid, text):
    key = _PENDING.get(str(tid))
    if not key:
        return False
    if text.lower() in {"cancel", "/cancel"}:
        _PENDING.pop(str(tid), None)
        _ui._send(app, tid, "✅ Change cancelled.", settings_keyboard(app, tid))
        return True
    try:
        value, unit = _set_from_text(app, tid, key, text)
        _PENDING.pop(str(tid), None)
        _ui._send(app, tid, f"✅ <b>Updated</b>  {html.escape(key)} = <b>{html.escape(value)} {html.escape(unit)}</b>", settings_keyboard(app, tid))
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}\nSend another value or <code>/cancel</code>.")
    return True


def _set_live_auto(app, tid, on):
    if on:
        ok, reason = can_start_live(app, tid)
        if not ok:
            raise ValueError(reason)
        set_user_value(app, tid, "enabled", "true")
        set_user_value(app, tid, "auto_trade_enabled", "true")
    else:
        set_user_value(app, tid, "auto_trade_enabled", "false")


def _setting_prompt(app, tid, key):
    if key not in SETTING_SPECS:
        raise ValueError("Unknown setting")
    lo, hi, unit = SETTING_SPECS[key]
    current = setting_value(app, tid, key)
    _PENDING[str(tid)] = key
    return "\n".join([
        "<b>✏️ Change SiBot setting</b>",
        DIV,
        f"Setting: <code>{html.escape(key)}</code>",
        f"Current: <b>{html.escape(str(current))}</b>",
        f"Allowed: <b>{lo:g} – {hi:g} {html.escape(unit)}</b>",
        "",
        "Send the new value, or <code>/cancel</code>.",
    ])


def _answer(app, cb, text=""):
    cqid = (cb or {}).get("id")
    if cqid:
        try:
            _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
        except Exception:
            pass


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data == "menu:sibot" or data.startswith("sibot:"):
            if not _ui._auth(app, tid):
                _answer(app, cb, "Not authorised")
                return
            _answer(app, cb)
            try:
                if data == "menu:sibot":
                    _render(app, tid, main_page(app, tid), sibot_keyboard(app, tid), cb)
                elif data == "sibot:settings":
                    _render(app, tid, settings_page(app, tid), settings_keyboard(app, tid), cb)
                elif data == "sibot:leaders":
                    _render(app, tid, leaders_page(app, tid), sibot_keyboard(app, tid), cb)
                elif data.startswith("sibot:leaders:"):
                    chain_id = data.rsplit(":", 1)[1]
                    _render(app, tid, leaders_page(app, tid, chain_id), _chain_picker(app, "sibot:leaders"), cb)
                elif data == "sibot:top20":
                    _render(app, tid, top20_summary_page(app, tid), _chain_picker(app, "sibot:top20"), cb)
                elif data.startswith("sibot:top20:"):
                    chain_id = data.rsplit(":", 1)[1]
                    _render(app, tid, top20_page(app, tid, chain_id), _chain_picker(app, "sibot:top20"), cb)
                elif data == "sibot:positions":
                    _render(app, tid, positions_page(app, tid), sibot_keyboard(app, tid), cb)
                elif data == "sibot:help":
                    _render(app, tid, help_page(), sibot_keyboard(app, tid), cb)
                elif data == "sibot:report":
                    _start_async(app, tid, "report")
                elif data == "sibot:refresh":
                    _start_async(app, tid, "refresh")
                elif data == "sibot:strategy:on":
                    set_user_value(app, tid, "enabled", "true")
                    _render(app, tid, main_page(app, tid), sibot_keyboard(app, tid), cb)
                elif data == "sibot:strategy:off":
                    set_user_value(app, tid, "enabled", "false")
                    _render(app, tid, main_page(app, tid), sibot_keyboard(app, tid), cb)
                elif data == "sibot:auto:arm":
                    text = "\n".join([
                        "<b>⚠️ Enable SiBot LIVE AUTO?</b>",
                        DIV,
                        "SiBot will be allowed to sign real BUY/SELL trades when every validation and platform gate passes.",
                        "",
                        "LIVE stays OFF unless you confirm below.",
                    ])
                    kb = {"inline_keyboard": [[
                        {"text": "✅ CONFIRM LIVE AUTO", "callback_data": "sibot:auto:confirm"},
                        {"text": "Cancel", "callback_data": "menu:sibot"},
                    ]]}
                    _render(app, tid, text, kb, cb)
                elif data == "sibot:auto:confirm":
                    _set_live_auto(app, tid, True)
                    _render(app, tid, main_page(app, tid), sibot_keyboard(app, tid), cb)
                elif data == "sibot:auto:off":
                    _set_live_auto(app, tid, False)
                    _render(app, tid, main_page(app, tid), sibot_keyboard(app, tid), cb)
                elif data == "sibot:partial:toggle":
                    cur = _bool(setting_value(app, tid, "mirror_partial_sells"), True)
                    set_user_value(app, tid, "mirror_partial_sells", "false" if cur else "true")
                    _render(app, tid, settings_page(app, tid), settings_keyboard(app, tid), cb)
                elif data.startswith("sibot:set:"):
                    key = data.split(":", 2)[2]
                    _render(app, tid, _setting_prompt(app, tid, key), {"inline_keyboard": [[{"text": "Cancel", "callback_data": "sibot:settings"}]]}, cb)
            except Exception as exc:
                _render(app, tid, f"❌ <b>SiBot</b>\n<code>{html.escape(str(exc)[:360])}</code>", sibot_keyboard(app, tid), cb)
            return

    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()
    if tid is not None and _ui._auth(app, tid) and _handle_pending(app, tid, text):
        return
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        parts = text.split()
        if cmd.startswith("/sibot"):
            try:
                require_user(app.csv_dir, tid, active=False)
                if cmd == "/sibot":
                    if len(parts) >= 2 and parts[1].lower() in {"on", "off"}:
                        set_user_value(app, tid, "enabled", "true" if parts[1].lower() == "on" else "false")
                    _ui._send(app, tid, main_page(app, tid), sibot_keyboard(app, tid))
                elif cmd == "/sibotstart":
                    if len(parts) != 2 or parts[1].upper() != "CONFIRM":
                        raise ValueError("Use /sibotstart CONFIRM")
                    _set_live_auto(app, tid, True)
                    _ui._send(app, tid, "✅ <b>SiBot LIVE AUTO is ON</b>\nNew leader trades can be copied only when every safety gate passes.", sibot_keyboard(app, tid))
                elif cmd == "/sibotstop":
                    set_user_value(app, tid, "enabled", "false")
                    set_user_value(app, tid, "auto_trade_enabled", "false")
                    _ui._send(app, tid, "✅ <b>SiBot stopped</b>\nNo new entries. Existing LIVE positions remain safety-monitored until closed.", sibot_keyboard(app, tid))
                elif cmd == "/sibotauto":
                    if len(parts) < 2 or parts[1].lower() not in {"on", "off"}:
                        raise ValueError("Use /sibotauto on CONFIRM or /sibotauto off")
                    on = parts[1].lower() == "on"
                    if on and (len(parts) < 3 or parts[2].upper() != "CONFIRM"):
                        raise ValueError("Use /sibotauto on CONFIRM")
                    _set_live_auto(app, tid, on)
                    _ui._send(app, tid, main_page(app, tid), sibot_keyboard(app, tid))
                elif cmd == "/sibotleaders":
                    _ui._send(app, tid, leaders_page(app, tid, parts[1] if len(parts) > 1 else None), sibot_keyboard(app, tid))
                elif cmd == "/sibottop20":
                    if len(parts) > 1:
                        _ui._send(app, tid, top20_page(app, tid, parts[1]), _chain_picker(app, "sibot:top20"))
                    else:
                        _ui._send(app, tid, top20_summary_page(app, tid), _chain_picker(app, "sibot:top20"))
                elif cmd == "/sibotpositions":
                    _ui._send(app, tid, positions_page(app, tid), sibot_keyboard(app, tid))
                elif cmd == "/sibotsettings":
                    _ui._send(app, tid, settings_page(app, tid), settings_keyboard(app, tid))
                elif cmd == "/sibothelp":
                    _ui._send(app, tid, help_page(), sibot_keyboard(app, tid))
                elif cmd == "/sibotreport":
                    _start_async(app, tid, "report")
                elif cmd == "/sibotrefresh":
                    _start_async(app, tid, "refresh")
                else:
                    mapping = {
                        "/sibotlookback": "lookback_days",
                        "/sibotleaderscount": "leaders_per_chain",
                        "/sibotallocation": "allocation_pct",
                        "/sibotmaxexposure": "max_exposure_pct",
                        "/sibotmintrades": "min_closed_trades",
                        "/sibotminwin": "min_win_rate_pct",
                        "/sibotsignalage": "max_signal_age_seconds",
                        "/sibotdeterioration": "max_entry_deterioration_pct",
                        "/sibotstoploss": "stop_loss_pct",
                        "/sibottakeprofit": "take_profit_pct",
                        "/sibotmaxpositions": "max_positions_per_chain",
                        "/sibotmaxhold": "max_hold_hours",
                    }
                    key = mapping.get(cmd)
                    if not key:
                        raise ValueError("Unknown SiBot command. Use /sibothelp")
                    if len(parts) != 2:
                        raise ValueError(f"Use {cmd} VALUE")
                    value, unit = _set_from_text(app, tid, key, parts[1])
                    _ui._send(app, tid, f"✅ <b>Updated</b>  {html.escape(key)} = <b>{html.escape(value)} {html.escape(unit)}</b>", settings_keyboard(app, tid))
            except Exception as exc:
                _ui._send(app, tid, f"❌ <b>SiBot</b>\n<code>{html.escape(str(exc)[:360])}</code>", sibot_keyboard(app, tid))
            return
    return _original_handle_update(app, update)


def start_menu_thread(app):
    try:
        start_workers(app)
    except Exception as exc:
        print("[sibot-start]", type(exc).__name__, exc)
    return _original_start_menu_thread(app)


def install():
    if getattr(_ui, "_sibot_patch_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui.start_menu_thread = start_menu_thread
    _ui.set_commands = set_commands
    _sibot._notify = _sibot_notify_clean
    _ui._sibot_patch_installed = True


install()
