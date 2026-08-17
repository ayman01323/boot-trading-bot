from __future__ import annotations

import html
import threading
from decimal import Decimal

from . import telegram_ui as _ui
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
_PENDING = {}
_BUSY = set()
_LOCK = threading.Lock()


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


def menu_keyboard(app=None, chat_id=None):
    kb = _original_menu_keyboard(app, chat_id)
    rows = list(kb.get("inline_keyboard") or [])
    row = [{"text": "🤖 SiBot / SiMo", "callback_data": "menu:sibot"}]
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
    enabled = str(cfg.get("enabled", "false")).lower() in {"1", "true", "yes", "on"}
    auto = str(cfg.get("auto_trade_enabled", "false")).lower() in {"1", "true", "yes", "on"}
    return {"inline_keyboard": [
        [{"text": f"{'🟢' if enabled else '⚪'} Strategy {'ON' if enabled else 'OFF'}", "callback_data": "sibot:strategy:off" if enabled else "sibot:strategy:on"},
         {"text": f"{'🔴 LIVE AUTO' if auto else '🧪 SHADOW'}", "callback_data": "sibot:auto:off" if auto else "sibot:auto:arm"}],
        [{"text": "🏆 SiMo Leaders", "callback_data": "sibot:leaders"}, {"text": "📋 Top 20", "callback_data": "sibot:top20"}],
        [{"text": "💼 Positions", "callback_data": "sibot:positions"}, {"text": "📊 Capital & P&L", "callback_data": "sibot:report"}],
        [{"text": "⚙️ Settings", "callback_data": "sibot:settings"}, {"text": "❓ How it works", "callback_data": "sibot:help"}],
        [{"text": "🔄 Refresh rankings", "callback_data": "sibot:refresh"}, {"text": "⬅️ Menu", "callback_data": "menu:home"}],
    ]}


def settings_keyboard(app, tid):
    keys = [
        ("lookback_days", "History days"), ("leaders_per_chain", "Leaders/chain"),
        ("allocation_pct", "Allocation %"), ("max_exposure_pct", "Max exposure %"),
        ("min_closed_trades", "Min trades"), ("min_win_rate_pct", "Min win rate"),
        ("max_signal_age_seconds", "Signal age"), ("max_entry_deterioration_pct", "Entry deterioration"),
        ("stop_loss_pct", "Stop loss"), ("take_profit_pct", "Take profit"),
        ("max_positions_per_chain", "Max positions"), ("max_hold_hours", "Max hold hours"),
    ]
    rows = []
    for i in range(0, len(keys), 2):
        rows.append([{"text": f"✏️ {label}", "callback_data": f"sibot:set:{key}"} for key, label in keys[i:i+2]])
    cfg = user_settings(app, tid, 0)
    partial = str(cfg.get("mirror_partial_sells", "true")).lower() in {"1", "true", "yes", "on"}
    rows += [[{"text": f"{'✅' if partial else '❌'} Mirror partial sells", "callback_data": "sibot:partial:toggle"}],
             [{"text": "⬅️ SiBot", "callback_data": "menu:sibot"}]]
    return {"inline_keyboard": rows}


def main_page(app, tid):
    cfg = user_settings(app, tid, 0)
    enabled = str(cfg.get("enabled", "false")).lower() in {"1", "true", "yes", "on"}
    auto = str(cfg.get("auto_trade_enabled", "false")).lower() in {"1", "true", "yes", "on"}
    leaders = leader_rows(app, tid); positions = position_rows(app, tid, open_only=True)
    wallet = _active_wallet(app, tid)
    return "\n".join([
        "<b>🤖 SiBot Strategy — SiMo Leader Copy</b>", "",
        f"Strategy: <b>{'🟢 ON' if enabled else '⚪ OFF'}</b>",
        f"Execution: <b>{'🔴 LIVE AUTO' if auto else '🧪 SHADOW / NO AUTO SIGNING'}</b>",
        f"Active wallet: <code>{html.escape(_short((wallet or {}).get('address') or 'not configured'))}</code>",
        f"History window: <b>{html.escape(str(cfg.get('lookback_days','60')))} days</b>",
        f"SiMo leaders/chain: <b>{html.escape(str(cfg.get('leaders_per_chain','2')))}</b>",
        f"New-position allocation: <b>{html.escape(str(cfg.get('allocation_pct','20')))}%</b>",
        f"Max SiBot exposure: <b>{html.escape(str(cfg.get('max_exposure_pct','60')))}%</b>",
        f"Current leaders: <b>{len(leaders)}</b> | Open positions: <b>{len(positions)}</b>", "",
        "<b>Start trading:</b> enable <code>/live on CONFIRM</code>, then use <code>/sibotstart CONFIRM</code>. For testing without signing, turn Strategy ON and leave LIVE AUTO off.", "",
        "<i>Turning SiBot off stops new copied entries. Existing LIVE positions continue to be monitored for leader exits and safety exits.</i>",
    ])


def settings_page(app, tid):
    c = user_settings(app, tid, 0)
    return "\n".join([
        "<b>⚙️ SiBot SETTINGS</b>", "",
        f"Lookback: <b>{c.get('lookback_days','60')} days</b>",
        f"Top wallets retained: <b>{c.get('top_wallets','20')}</b>",
        f"SiMo leaders/chain: <b>{c.get('leaders_per_chain','2')}</b>",
        f"Allocation/trade: <b>{c.get('allocation_pct','20')}%</b>",
        f"Maximum SiBot exposure: <b>{c.get('max_exposure_pct','60')}%</b>",
        f"Minimum matched closed trades: <b>{c.get('min_closed_trades','50')}</b>",
        f"Minimum win rate: <b>{c.get('min_win_rate_pct','55')}%</b>",
        f"Maximum signal age: <b>{c.get('max_signal_age_seconds','20')}s</b>",
        f"Maximum worse entry: <b>{c.get('max_entry_deterioration_pct','1.5')}%</b>",
        f"Maximum quote round-trip loss: <b>{c.get('max_roundtrip_loss_pct','3')}%</b>",
        f"Max positions/chain: <b>{c.get('max_positions_per_chain','5')}</b>",
        f"Stop loss: <b>{c.get('stop_loss_pct','10')}%</b>",
        f"Take profit: <b>{c.get('take_profit_pct','25')}%</b>",
        f"Leader exit minimum profit: <b>{c.get('min_exit_profit_pct','0.10')}%</b>",
        f"Max hold profit threshold: <b>{c.get('max_hold_hours','24')}h</b>",
        f"Mirror primary leader partial sells: <b>{'ON' if str(c.get('mirror_partial_sells','true')).lower() in {'1','true','yes','on'} else 'OFF'}</b>", "",
        "Settings are per Telegram user and can also be overridden per chain in <code>user_trading_settings.csv</code>.",
    ])


def leaders_page(app, tid, chain=None):
    target = _chain(app, chain) if chain else None
    rows = leader_rows(app, tid, target.chain_id if target else None)
    L = ["<b>🏆 SiMo LEADERS</b>", "Top profitable qualifying wallets selected from the current SiBot lookback.", ""]
    if not rows:
        L += ["No verified SiMo leaders yet.", "The history worker may still be reconstructing wallet BUY→SELL records, or current minimum trade/win-rate rules are too strict."]
        return "\n".join(L)
    last_chain = None
    for r in rows:
        if r["chain_id"] != last_chain:
            c = _chain(app, r["chain_id"]); L.append(f"<b>{html.escape((c.name if c else r['chain_slug']))}</b>"); last_chain = r["chain_id"]
        c = _chain(app, r["chain_id"]); sym = c.native_symbol if c else "native"
        prefix = "🥇" if int(r['rank']) == 1 else "🥈" if int(r['rank']) == 2 else "•"
        L.append(prefix + f" #{r['rank']} <code>{html.escape(_short(r['wallet']))}</code> — net <b>{Decimal(str(r['net_profit_native'])):+.8f} {html.escape(sym)}</b> | WR {float(r['win_rate']):.1f}% | {r['closed_trades']} trades")
    return "\n".join(L)


def top20_page(app, tid, chain=None):
    target = _chain(app, chain) if chain else None
    rows = ranking_rows(app, tid, target.chain_id if target else None)
    L = ["<b>📋 SiBot TOP-20 PROFITABLE WALLETS</b>", "Ranked by realised net profit over your configured history window. Profit must exceed losses.", ""]
    if not rows:
        L.append("No qualifying ranked wallets yet.")
        return "\n".join(L)
    last_chain = None
    for r in rows:
        if r["chain_id"] != last_chain:
            c = _chain(app, r["chain_id"]); L += [f"<b>{html.escape(c.name if c else r['chain_slug'])}</b>"]; last_chain = r["chain_id"]
        c = _chain(app, r["chain_id"]); sym = c.native_symbol if c else "native"
        mark = "✅" if int(r.get("history_complete") or 0) else "⚠️"
        L.append(f"{mark} <b>#{r['rank']}</b> <code>{html.escape(_short(r['wallet']))}</code> | net {Decimal(str(r['net_profit_native'])):+.8f} {html.escape(sym)} | profit {Decimal(str(r['gross_profit_native'])):.8f} | loss {Decimal(str(r['gross_loss_native'])):.8f} | WR {float(r['win_rate']):.1f}% | {r['closed_trades']} trades")
    return "\n".join(L)


def positions_page(app, tid):
    rows = position_rows(app, tid, open_only=True)
    L = ["<b>💼 SiBot OPEN POSITIONS</b>", ""]
    if not rows:
        return "\n".join(L + ["No open SiBot positions."])
    for p in rows[:40]:
        c = _chain(app, p["chain_id"]); sym = c.native_symbol if c else "native"
        leaders = ", ".join(("★" if int(x.get("primary_flag") or 0) else "+") + _short(x.get("leader_wallet")) for x in p.get("leaders", []))
        L += [f"<b>{html.escape(p.get('symbol') or _short(p['token']))}</b> — {html.escape((c.name if c else p['chain_slug']))} [{html.escape(p['mode'])}]",
              f"Leaders: <code>{html.escape(leaders)}</code>",
              f"Entry: {Decimal(str(p['entry_input_native'])):.8f} {html.escape(sym)} | unrealised: <b>{Decimal(str(p.get('unrealised_net_native') or 0)):+.8f}</b> ({float(p.get('unrealised_pct') or 0):+.2f}%)",
              f"Leader exit pending: <b>{'YES' if int(p.get('leader_exit_pending') or 0) else 'NO'}</b>", ""]
    return "\n".join(L)


def help_page():
    return "\n".join([
        "<b>❓ HOW SiBot / SiMo WORKS</b>", "",
        "1. The learning system finds trading wallets on each enabled EVM chain.",
        "2. SiBot reconstructs direct native↔token BUY→SELL history and calculates realised P&L after gas.",
        "3. Over the selected lookback (default 60 days), only wallets whose realised profits exceed realised losses and pass your minimum trade/win-rate rules enter the Top-20.",
        "4. The highest net-profit wallets become <b>SiMo Leaders</b> (default 2 per chain).",
        "5. SiBot watches newly confirmed blocks for a SiMo leader BUY. It does not blindly copy an old transaction.",
        "6. Before copying, your bot checks signal age, current direct quote, entry deterioration versus the leader, round-trip sellability and LIVE product policy.",
        "7. Position size defaults to 20% of SiBot chain capital, bounded by max exposure, gas reserve and the existing wallet max-trade limit.",
        "8. When the primary SiMo leader sells, SiBot mirrors the exit if our estimated exit is net profitable. If it is not yet profitable it marks EXIT PENDING and keeps checking.",
        "9. Independent stop-loss/take-profit rules remain active; a leader can never force the bot to ignore capital protection.",
        "10. SHADOW mode records hypothetical positions without signing. LIVE AUTO requires your LIVE switch plus the MASTER LIVE/AUTO safety gates.", "",
        "<b>Start:</b> <code>/live on CONFIRM</code> then <code>/sibotstart CONFIRM</code>.",
        "<b>Shadow only:</b> <code>/sibot on</code> and leave SiBot auto off.",
        "<b>Stop new entries:</b> <code>/sibotstop</code>. Existing LIVE positions continue to be risk-monitored.",
    ])


def _fmt_usd(v):
    return f"${Decimal(str(v)):,.2f}"


def report_text(app, tid):
    d = user_dashboard_data(app, tid); sp = performance(app, tid)
    active = next((w for w in d["wallets"] if str(w.get("active", "")).lower() in {"1","true","yes","on"}), d["wallets"][0] if d["wallets"] else None)
    L = ["<b>📊 SiBot CAPITAL & P&L REPORT</b>", ""]
    if active:
        L += [f"Active wallet: <code>{html.escape(_short(active.get('address')))}</code>", ""]
    total_capital = Decimal(0); total_existing = Decimal(0); total_sibot_real = Decimal(0); total_sibot_unreal = Decimal(0)
    existing_by_slug = d["performance"].get("by_chain", {})
    for c in load_chains(app, enabled_only=True):
        cap = Decimal(0)
        if active:
            snap = next((x for x in active.get("chains", []) if int(x["chain_id"]) == int(c.chain_id)), None)
            if snap: cap = Decimal(str(snap.get("capital_usd") or 0))
        native_price = Decimal(str(d.get("native_prices", {}).get(c.slug) or 0))
        existing_native = Decimal(str((existing_by_slug.get(c.slug) or {}).get("net") or 0))
        srow = sp["by_chain"].get(c.chain_id, {})
        sreal = Decimal(str(srow.get("realised") or 0)); sunreal = Decimal(str(srow.get("unrealised") or 0))
        existing_usd = existing_native * native_price; sreal_usd = sreal * native_price; sunreal_usd = sunreal * native_price
        total_capital += cap; total_existing += existing_usd; total_sibot_real += sreal_usd; total_sibot_unreal += sunreal_usd
        L += [f"<b>{html.escape(c.name)}</b>", f"Wallet capital: <b>{_fmt_usd(cap)}</b>",
              f"Existing bot realised P&L: <b>{existing_native:+.8f} {html.escape(c.native_symbol)}</b> ({_fmt_usd(existing_usd)})",
              f"SiBot realised P&L: <b>{sreal:+.8f} {html.escape(c.native_symbol)}</b> ({_fmt_usd(sreal_usd)})",
              f"SiBot open/unrealised: <b>{sunreal:+.8f} {html.escape(c.native_symbol)}</b> ({_fmt_usd(sunreal_usd)})", ""]
    combined_real = total_existing + total_sibot_real
    L += ["<b>OVERALL</b>", f"Active-wallet priced capital: <b>{_fmt_usd(total_capital)}</b>",
          f"Existing bot realised P&L: <b>{_fmt_usd(total_existing)}</b>", f"SiBot realised P&L: <b>{_fmt_usd(total_sibot_real)}</b>",
          f"Combined realised P&L: <b>{_fmt_usd(combined_real)}</b>", f"SiBot unrealised P&L: <b>{_fmt_usd(total_sibot_unreal)}</b>",
          f"Combined realised + SiBot open: <b>{_fmt_usd(combined_real + total_sibot_unreal)}</b>", "",
          "<i>USD values use the dashboard's current prices; unpriced wallet tokens are excluded from the capital total. Realised P&L is accounting from recorded successful executions, not the wallet's lifetime blockchain P&L.</i>"]
    return "\n".join(L)


def _send_report_worker(app, tid, key):
    try:
        _ui._send(app, tid, report_text(app, tid), sibot_keyboard(app, tid))
    except Exception as exc:
        _ui._send(app, tid, f"❌ SiBot report failed: {html.escape(str(exc)[:300])}", sibot_keyboard(app, tid))
    finally:
        with _LOCK: _BUSY.discard(key)


def _refresh_worker(app, tid, key):
    try:
        request_history_refresh(app, tid); refresh_all_rankings(app, tid)
        _ui._send(app, tid, "✅ SiBot ranking refresh requested. Stored histories were re-ranked immediately; remote 365-day backfill continues in the SiBot history worker.", sibot_keyboard(app, tid))
    except Exception as exc:
        _ui._send(app, tid, f"❌ Refresh failed: {html.escape(str(exc)[:300])}", sibot_keyboard(app, tid))
    finally:
        with _LOCK: _BUSY.discard(key)


def _start_async(app, tid, kind):
    key = (str(tid), kind)
    with _LOCK:
        if key in _BUSY: return False
        _BUSY.add(key)
    if kind == "report":
        target = _send_report_worker; msg = "⏳ Reading live wallet balances and SiBot P&L across chains…"
    else:
        target = _refresh_worker; msg = "⏳ Refreshing SiBot rankings and queuing historical backfill…"
    _ui._send(app, tid, msg)
    threading.Thread(target=target, args=(app, tid, key), daemon=True, name=f"sibot-{kind}-{tid}").start()
    return True


def _set_from_text(app, tid, key, raw):
    if key not in SETTING_SPECS: raise ValueError("Unknown SiBot setting")
    lo, hi, unit = SETTING_SPECS[key]
    v = parse_float(raw, minimum=lo, maximum=hi, name=f"SiBot {key}")
    if key in {"lookback_days","leaders_per_chain","min_closed_trades","max_signal_age_seconds","max_positions_per_chain","max_hold_hours"}:
        value = str(int(v))
    else:
        value = f"{v:g}"
    set_user_value(app, tid, key, value)
    return value, unit


def _handle_pending(app, tid, text):
    key = _PENDING.get(str(tid))
    if not key: return False
    if text.lower() in {"cancel", "/cancel"}:
        _PENDING.pop(str(tid), None); _ui._send(app, tid, "SiBot setting change cancelled.", settings_keyboard(app, tid)); return True
    try:
        value, unit = _set_from_text(app, tid, key, text)
        _PENDING.pop(str(tid), None)
        _ui._send(app, tid, f"✅ SiBot <b>{html.escape(key)}</b> = <b>{html.escape(value)} {html.escape(unit)}</b>.", settings_keyboard(app, tid))
    except Exception as exc:
        _ui._send(app, tid, f"❌ {html.escape(str(exc))}\nSend another value or <code>/cancel</code>.")
    return True


def _set_live_auto(app, tid, on):
    if on:
        ok, reason = can_start_live(app, tid)
        if not ok: raise ValueError(reason)
        set_user_value(app, tid, "enabled", "true")
        set_user_value(app, tid, "auto_trade_enabled", "true")
    else:
        set_user_value(app, tid, "auto_trade_enabled", "false")


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id"); data = str(cb.get("data") or ""); cqid = cb.get("id")
        if data == "menu:sibot" or data.startswith("sibot:"):
            if not _ui._auth(app, tid):
                if cqid: _ui.answer_callback_query(app.telegram_bot_token, cqid, "Not authorised")
                return
            try:
                if cqid: _ui.answer_callback_query(app.telegram_bot_token, cqid)
                if data == "menu:sibot": _ui._send(app, tid, main_page(app, tid), sibot_keyboard(app, tid))
                elif data == "sibot:settings": _ui._send(app, tid, settings_page(app, tid), settings_keyboard(app, tid))
                elif data == "sibot:leaders": _ui._send(app, tid, leaders_page(app, tid), sibot_keyboard(app, tid))
                elif data == "sibot:top20": _ui._send(app, tid, top20_page(app, tid), sibot_keyboard(app, tid))
                elif data == "sibot:positions": _ui._send(app, tid, positions_page(app, tid), sibot_keyboard(app, tid))
                elif data == "sibot:help": _ui._send(app, tid, help_page(), sibot_keyboard(app, tid))
                elif data == "sibot:report": _start_async(app, tid, "report")
                elif data == "sibot:refresh": _start_async(app, tid, "refresh")
                elif data == "sibot:strategy:on": set_user_value(app, tid, "enabled", "true"); _ui._send(app, tid, main_page(app, tid), sibot_keyboard(app, tid))
                elif data == "sibot:strategy:off": set_user_value(app, tid, "enabled", "false"); _ui._send(app, tid, main_page(app, tid), sibot_keyboard(app, tid))
                elif data == "sibot:auto:arm":
                    _ui._send(app, tid, "⚠️ <b>Enable SiBot LIVE AUTO?</b>\nThis allows SiBot to sign real copied BUY/SELL trades when all gates pass.", {"inline_keyboard":[[{"text":"✅ CONFIRM LIVE AUTO","callback_data":"sibot:auto:confirm"},{"text":"Cancel","callback_data":"menu:sibot"}]]})
                elif data == "sibot:auto:confirm": _set_live_auto(app, tid, True); _ui._send(app, tid, main_page(app, tid), sibot_keyboard(app, tid))
                elif data == "sibot:auto:off": _set_live_auto(app, tid, False); _ui._send(app, tid, main_page(app, tid), sibot_keyboard(app, tid))
                elif data == "sibot:partial:toggle":
                    cur = str(setting_value(app, tid, "mirror_partial_sells") or "true").lower() in {"1","true","yes","on"}
                    set_user_value(app, tid, "mirror_partial_sells", "false" if cur else "true"); _ui._send(app, tid, settings_page(app, tid), settings_keyboard(app, tid))
                elif data.startswith("sibot:set:"):
                    key = data.split(":", 2)[2]
                    if key not in SETTING_SPECS: raise ValueError("Unknown setting")
                    lo, hi, unit = SETTING_SPECS[key]; _PENDING[str(tid)] = key
                    _ui._send(app, tid, f"Send the new <b>{html.escape(key)}</b> value ({lo:g}–{hi:g} {html.escape(unit)}), or <code>/cancel</code>.")
            except Exception as exc:
                _ui._send(app, tid, f"❌ SiBot: {html.escape(str(exc)[:400])}", sibot_keyboard(app, tid))
            return
    m = update.get("message") or {}; tid = (m.get("chat") or {}).get("id"); text = str(m.get("text") or "").strip()
    if tid is not None and _ui._auth(app, tid) and _handle_pending(app, tid, text): return
    if tid is not None and text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower(); parts = text.split()
        if cmd.startswith("/sibot"):
            try:
                require_user(app.csv_dir, tid, active=False)
                if cmd == "/sibot":
                    if len(parts) >= 2 and parts[1].lower() in {"on","off"}:
                        set_user_value(app, tid, "enabled", "true" if parts[1].lower()=="on" else "false")
                    _ui._send(app, tid, main_page(app, tid), sibot_keyboard(app, tid))
                elif cmd == "/sibotstart":
                    if len(parts) != 2 or parts[1].upper() != "CONFIRM": raise ValueError("Use /sibotstart CONFIRM")
                    _set_live_auto(app, tid, True); _ui._send(app, tid, "✅ <b>SiBot LIVE AUTO started.</b> New SiMo leader trades may now be copied when all validation gates pass.", sibot_keyboard(app, tid))
                elif cmd == "/sibotstop":
                    set_user_value(app, tid, "enabled", "false"); set_user_value(app, tid, "auto_trade_enabled", "false")
                    _ui._send(app, tid, "✅ SiBot stopped for new entries. Existing LIVE SiBot positions remain safety-monitored until closed.", sibot_keyboard(app, tid))
                elif cmd == "/sibotauto":
                    if len(parts) < 2 or parts[1].lower() not in {"on","off"}: raise ValueError("Use /sibotauto on CONFIRM or /sibotauto off")
                    on = parts[1].lower()=="on"
                    if on and (len(parts)<3 or parts[2].upper()!="CONFIRM"): raise ValueError("Use /sibotauto on CONFIRM")
                    _set_live_auto(app, tid, on); _ui._send(app, tid, main_page(app, tid), sibot_keyboard(app, tid))
                elif cmd == "/sibotleaders": _ui._send(app, tid, leaders_page(app, tid, parts[1] if len(parts)>1 else None), sibot_keyboard(app, tid))
                elif cmd == "/sibottop20": _ui._send(app, tid, top20_page(app, tid, parts[1] if len(parts)>1 else None), sibot_keyboard(app, tid))
                elif cmd == "/sibotpositions": _ui._send(app, tid, positions_page(app, tid), sibot_keyboard(app, tid))
                elif cmd == "/sibotsettings": _ui._send(app, tid, settings_page(app, tid), settings_keyboard(app, tid))
                elif cmd == "/sibothelp": _ui._send(app, tid, help_page(), sibot_keyboard(app, tid))
                elif cmd == "/sibotreport": _start_async(app, tid, "report")
                elif cmd == "/sibotrefresh": _start_async(app, tid, "refresh")
                else:
                    mapping = {"/sibotlookback":"lookback_days","/sibotleaderscount":"leaders_per_chain","/sibotallocation":"allocation_pct","/sibotmaxexposure":"max_exposure_pct","/sibotmintrades":"min_closed_trades","/sibotminwin":"min_win_rate_pct","/sibotsignalage":"max_signal_age_seconds","/sibotdeterioration":"max_entry_deterioration_pct","/sibotstoploss":"stop_loss_pct","/sibottakeprofit":"take_profit_pct","/sibotmaxpositions":"max_positions_per_chain","/sibotmaxhold":"max_hold_hours"}
                    key = mapping.get(cmd)
                    if not key: raise ValueError("Unknown SiBot command. Use /sibothelp")
                    if len(parts) != 2: raise ValueError(f"Use {cmd} VALUE")
                    value, unit = _set_from_text(app, tid, key, parts[1]); _ui._send(app, tid, f"✅ {html.escape(key)} = <b>{html.escape(value)} {html.escape(unit)}</b>", settings_keyboard(app, tid))
            except Exception as exc:
                _ui._send(app, tid, f"❌ SiBot: {html.escape(str(exc)[:400])}", sibot_keyboard(app, tid))
            return
    return _original_handle_update(app, update)


def start_menu_thread(app):
    try: start_workers(app)
    except Exception as exc: print("[sibot-start]", type(exc).__name__, exc)
    return _original_start_menu_thread(app)


def install():
    if getattr(_ui, "_sibot_patch_installed", False): return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui.start_menu_thread = start_menu_thread
    _ui._sibot_patch_installed = True


install()
