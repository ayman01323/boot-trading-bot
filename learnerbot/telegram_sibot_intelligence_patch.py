from __future__ import annotations

import html
import json
import threading
import time
from decimal import Decimal

from . import sibot as _sibot
from . import sibot_intelligence_patch as _intel
from . import solana_sibot as _sol
from . import telegram_sibot_patch as _tg
from . import telegram_ui as _ui
from .config import load_chains

_original_handle_update = _ui.handle_update
_original_start_menu_thread = _ui.start_menu_thread
_original_sibot_keyboard = _tg.sibot_keyboard
_original_chain_picker = _tg._chain_picker
_original_top20_summary = _tg.top20_summary_page
_original_main_page = _tg.main_page
_original_help_page = _tg.help_page
_BUSY = set()
_LOCK = threading.Lock()
DIV = _tg.DIV


def _sol_link(address: str) -> str:
    cfg = _sol.settings(_APP_HOLDER.get("app")) if _APP_HOLDER.get("app") else {"explorer_url": _sol.DEFAULT_EXPLORER}
    base = str(cfg.get("explorer_url") or _sol.DEFAULT_EXPLORER).rstrip("/")
    a = str(address or "")
    label = html.escape(a if len(a) <= 18 else f"{a[:8]}…{a[-6:]}")
    return f'<a href="{html.escape(base + "/account/" + a, quote=True)}">🔎 {label}</a>'


def _sol_tx_link(app, signature: str) -> str:
    base = str(_sol.settings(app).get("explorer_url") or _sol.DEFAULT_EXPLORER).rstrip("/")
    sig = str(signature or "")
    label = html.escape(sig if len(sig) <= 18 else f"{sig[:8]}…{sig[-6:]}")
    return f'<a href="{html.escape(base + "/tx/" + sig, quote=True)}">{label}</a>' if sig else "—"


def _evm_wallet_link(chain, address: str) -> str:
    explorer = str(getattr(chain, "explorer_url", "") or "").rstrip("/")
    label = html.escape(_tg._short(address))
    if not explorer:
        return f"<code>{label}</code>"
    return f'<a href="{html.escape(explorer + "/address/" + address, quote=True)}">🔎 {label}</a>'


def _evm_tx_link(chain, tx: str) -> str:
    explorer = str(getattr(chain, "explorer_url", "") or "").rstrip("/")
    label = html.escape(_tg._short(tx))
    if not explorer or not tx:
        return f"<code>{label}</code>" if tx else "—"
    return f'<a href="{html.escape(explorer + "/tx/" + tx, quote=True)}">{label}</a>'


_APP_HOLDER = {}


def sibot_keyboard(app, tid):
    kb = _original_sibot_keyboard(app, tid)
    rows = list(kb.get("inline_keyboard") or [])
    intel_row = [
        {"text": "🧬 Wallet Intelligence", "callback_data": "sibot:intel"},
        {"text": "🟣 Solana", "callback_data": "sibot:solana"},
    ]
    if not any(any(b.get("callback_data") == "sibot:intel" for b in row) for row in rows):
        insert_at = 3 if len(rows) >= 3 else len(rows)
        rows.insert(insert_at, intel_row)
        rows.insert(insert_at + 1, [{"text": "🔎 Last Entry Study", "callback_data": "sibot:lastentries"}])
    return {"inline_keyboard": rows}


def _chain_picker(app, prefix, back="menu:sibot"):
    kb = _original_chain_picker(app, prefix, back)
    rows = list(kb.get("inline_keyboard") or [])
    if prefix in {"sibot:top20", "sibot:leaders"}:
        sol_button = {"text": "🟣 SOLANA", "callback_data": f"{prefix}:solana"}
        if rows and len(rows[-1]) == 1 and rows[-1][0].get("callback_data") == back:
            rows.insert(len(rows) - 1, [sol_button])
        else:
            rows.append([sol_button])
    return {"inline_keyboard": rows}


def main_page(app, tid):
    text = _original_main_page(app, tid)
    try:
        s = _sol.status(app)
        extra = f"\n🟣 Solana research: <b>{s['candidates']}</b> candidates • <b>{len(_sol.ranking_rows(app, tid))}</b> Top-20"
    except Exception:
        extra = "\n🟣 Solana research: <b>starting</b>"
    return text + extra


def top20_summary_page(app, tid):
    text = _original_top20_summary(app, tid)
    try:
        n = len(_sol.ranking_rows(app, tid))
        sol_line = f"🟣 <b>Solana</b>  •  <b>{n}</b> profitable wallet{'s' if n != 1 else ''}"
        if "<i>" in text:
            head, tail = text.split("<i>", 1)
            text = head.rstrip() + "\n" + sol_line + "\n\n<i>" + tail
        else:
            text += "\n" + sol_line
    except Exception:
        pass
    return text


def help_page():
    return _original_help_page() + "\n\n" + "\n".join([
        "<b>🧬 Intelligence additions</b>",
        "• Last Entry Study compares the leader's current step with recent matched BUY→SELL behaviour.",
        "• Cross-chain profiles join the same <code>0x…</code> EVM address across enabled EVM chains and use USD evidence where available.",
        "• Adaptive exits add break-even, trailing-profit and leader-exit loss-cap protection.",
        "• Solana is currently analysis + SHADOW only; LIVE Solana signing is intentionally disabled.",
    ])


def solana_page(app, tid):
    cfg = _sol.settings(app)
    s = _sol.status(app)
    rows = _sol.ranking_rows(app, tid)
    leaders = _sol.leader_rows(app, tid)
    positions = _sol.position_rows(app, tid, open_only=True)
    return "\n".join([
        "<b>🟣 SiBot — Solana</b>", DIV,
        "🧪 <b>ANALYSIS + SHADOW</b>  •  🔒 LIVE signing disabled",
        "",
        f"👀 Candidates discovered  <b>{s['candidates']}</b>",
        f"📚 Wallet histories with trades  <b>{s['histories']}</b>",
        f"🔁 Reconstructed closed trades  <b>{s['closed_trades']}</b>",
        f"📈 Profitable Top-20 wallets  <b>{len(rows)}</b>",
        f"🏆 Leaders  <b>{len(leaders)}</b>",
        f"💼 Open SHADOW positions  <b>{len(positions)}</b>",
        "",
        f"🗓 Lookback  <b>{html.escape(str(cfg.get('lookback_days','60')))} days</b>",
        f"💵 SHADOW entry  <b>{html.escape(str(cfg.get('shadow_allocation_sol','.05')))} SOL</b>",
        "",
        "<i>Discovery reads finalized Solana blocks. Historical ranking currently uses conservative SOL↔token matched cycles.</i>",
    ])


def solana_keyboard():
    return {"inline_keyboard": [
        [{"text": "📈 Top 20", "callback_data": "sibot:solana:top20"}, {"text": "🏆 Leaders", "callback_data": "sibot:solana:leaders"}],
        [{"text": "💼 SHADOW Positions", "callback_data": "sibot:solana:positions"}, {"text": "🔄 Refresh", "callback_data": "sibot:solana:refresh"}],
        [{"text": "⬅️ SiBot", "callback_data": "menu:sibot"}],
    ]}


def solana_top20_page(app, tid):
    rows = _sol.ranking_rows(app, tid)
    L = ["<b>🟣 Solana — SiBot Top 20</b>", DIV, "<b>Rule:</b> 60-day measured SOL gains &gt; SOL losses and net &gt; 0.", ""]
    if not rows:
        L += ["⏳ No profitable reconstructed Solana wallet yet.", "Discovery/history backfill continues automatically from finalized blocks."]
        return "\n".join(L)
    for r in rows[:20]:
        rank = int(r["rank"])
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "▫️"
        L.append(f"{medal} <b>#{rank}</b> {_sol_link(r['wallet'])}")
        L.append(f"   💰 Net <b>{Decimal(str(r['net_profit_sol'])):+.6f} SOL</b> • ✅ {Decimal(str(r['gross_profit_sol'])):.6f} • 🔻 {Decimal(str(r['gross_loss_sol'])):.6f}")
        L.append(f"   🔁 {int(r['closed_trades'])} proven • 🎯 {float(r['win_rate']):.1f}% positive")
    return "\n".join(L)


def solana_leaders_page(app, tid):
    rows = _sol.leader_rows(app, tid)
    L = ["<b>🏆 Solana SiBot Leaders</b>", DIV]
    if not rows:
        return "\n".join(L + ["", "⏳ Top 20 may still be building or no wallet yet passes 5 results + 50% positive."])
    for r in rows:
        rank = int(r["rank"])
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🏅"
        L += [
            "",
            f"{medal} <b>#{rank}</b> {_sol_link(r['wallet'])}",
            f"   💰 {Decimal(str(r['net_profit_sol'])):+.6f} SOL • 🎯 {float(r['win_rate']):.1f}% • 🔁 {int(r['closed_trades'])}",
        ]
    return "\n".join(L)


def solana_positions_page(app, tid):
    rows = _sol.position_rows(app, tid, open_only=True)
    L = ["<b>💼 Solana SiBot SHADOW Positions</b>", DIV]
    if not rows:
        return "\n".join(L + ["", "✅ No open Solana SHADOW positions."])
    for p in rows[:20]:
        pct = Decimal(str(p.get("unrealised_pct") or 0))
        pnl = Decimal(str(p.get("unrealised_net_sol") or 0))
        icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        mint = str(p["mint"])
        L += [
            "",
            f"🪙 <code>{html.escape(mint[:8] + '…' + mint[-6:])}</code> • leader {_sol_link(p['leader_wallet'])}",
            f"{icon} P&L <b>{pnl:+.6f} SOL</b> ({pct:+.2f}%) • peak {float(p.get('peak_unrealised_pct') or 0):+.2f}%",
            f"🔁 Leader BUY signals followed: <b>{int(p.get('signal_count') or 1)}</b>",
            f"{'⏳ Leader exit pending' if int(p.get('leader_exit_pending') or 0) else '📡 Monitoring leader + Jupiter exit quote'}",
        ]
    return "\n".join(L)


def _profile_buttons(app, tid):
    rows = _intel.profile_rows(app, tid)[:8]
    buttons = []
    for r in rows:
        a = str(r["wallet"])
        buttons.append([{"text": f"🧬 {a[:6]}…{a[-4:]}  {float(r['confidence']):.0f}%", "callback_data": f"sibot:profile:{a}"}])
    buttons.append([{"text": "🔎 Last Entry Study", "callback_data": "sibot:lastentries"}])
    buttons.append([{"text": "⬅️ SiBot", "callback_data": "menu:sibot"}])
    return {"inline_keyboard": buttons}


def intelligence_page(app, tid):
    try:
        _intel.refresh_crosschain_profiles(app)
    except Exception:
        pass
    rows = _intel.profile_rows(app, tid)
    L = ["<b>🧬 SiBot Wallet Intelligence</b>", DIV,
         "Same <code>0x…</code> address is correlated across enabled EVM chains. USD profit evidence is used where stored.", ""]
    if not rows:
        return "\n".join(L + ["⏳ Cross-chain profiles will appear after EVM Top-20 rankings exist."])
    for r in rows[:8]:
        L += [
            f"{_evm_profile_link(app, r['wallet'])}  •  confidence <b>{float(r['confidence']):.0f}%</b>",
            f"   🌐 {int(r['profitable_chains'])}/{int(r['chains_seen'])} profitable chains • 🔁 {int(r['proven_results'])} proven • 🎯 {float(r['positive_ratio']):.1f}%",
        ]
    L += ["", "<i>Solana addresses use a different key format and are not falsely joined to an EVM 0x identity.</i>"]
    return "\n".join(L)


def _evm_profile_link(app, wallet):
    # Use the first enabled explorer only as a convenient wallet link on the summary.
    chain = next(iter(load_chains(app, enabled_only=True)), None)
    return _evm_wallet_link(chain, wallet) if chain else f"<code>{html.escape(_tg._short(wallet))}</code>"


def profile_page(app, tid, wallet):
    p = _intel.crosschain_profile(app, tid, wallet)
    L = ["<b>🧬 Cross-Chain Wallet Profile</b>", DIV,
         f"<code>{html.escape(wallet)}</code>",
         f"Confidence  <b>{float(p['confidence']):.0f}%</b>  •  profitable chains <b>{p['profitable_chains']}/{p['chains_seen']}</b>",
         f"Proven results  <b>{p['proven_results']}</b>  •  positive <b>{p['positive_ratio']:.1f}%</b>",
         f"Stored USD net evidence  <b>${p['net_usd']:,.2f}</b>", ""]
    chain_map = {c.chain_id: c for c in load_chains(app, enabled_only=True)}
    for d in p["details"]:
        c = chain_map.get(int(d["chain_id"]))
        wallet_link = _evm_wallet_link(c, wallet) if c else html.escape(_tg._short(wallet))
        net = f"${float(d['net_usd']):,.2f}" if float(d.get("net_usd") or 0) else f"{d.get('net_native') or 'n/a'} native"
        ratio = int(d.get("positive") or 0) / max(1, int(d.get("proven") or 0)) * 100
        L += [
            f"<b>🌐 {html.escape(d['chain_name'])}</b>  {wallet_link}",
            f"   💰 {html.escape(net)} • 🔁 {int(d['proven'])} • 🎯 {ratio:.1f}%" + (f" • rank #{int(d['rank'])}" if d.get("rank") else ""),
        ]
    return "\n".join(L)


def last_entries_page(app, tid):
    chain_map = {c.chain_id: c for c in load_chains(app, enabled_only=True)}
    rows = _sibot.leader_rows(app, tid)
    L = ["<b>🔎 SiBot Last Entry Study</b>", DIV,
         "For each selected EVM leader: latest direct action, latest direct BUY, and recent matched-trade behaviour.", ""]
    if not rows:
        L.append("⏳ No EVM leaders selected yet.")
    for r in rows:
        c = chain_map.get(int(r["chain_id"]))
        study = _intel.study_row(app, int(r["chain_id"]), r["wallet"])
        if not study:
            try:
                study = _intel.refresh_one_study(app, c, r["wallet"], fetch_remote=False) if c else None
            except Exception:
                study = None
        L += ["", f"<b>🌐 {html.escape(c.name if c else str(r['chain_id']))}</b>  {_evm_wallet_link(c, r['wallet'])}"]
        if not study:
            L.append("   ⏳ Study pending historical backfill.")
            continue
        hold_min = float(study.get("median_hold_seconds") or 0) / 60.0
        L.append(f"   📚 {int(study.get('sample_trades') or 0)} matched • 🎯 {float(study.get('win_rate') or 0):.1f}% • median hold {hold_min:.1f}m")
        if study.get("latest_buy_tx"):
            age = max(0, int(time.time()) - int(study.get("latest_buy_ts") or 0))
            L.append(f"   🟢 Last BUY: <b>{html.escape(study.get('latest_buy_symbol') or _tg._short(study.get('latest_buy_token')))}</b> • {Decimal(str(study.get('latest_buy_native') or 0)):.6f} {html.escape(c.native_symbol if c else 'native')} • {age//60}m ago")
            L.append(f"      TX {_evm_tx_link(c, study.get('latest_buy_tx'))}")
        elif study.get("latest_action"):
            L.append(f"   Latest direct action: <b>{html.escape(study['latest_action'])}</b> {_evm_tx_link(c, study.get('latest_action_tx'))}")
        else:
            L.append("   Latest direct BUY is still being reconstructed.")
    # Include Solana leaders' latest reconstructed closed trade as a study reference.
    sol_leaders = _sol.leader_rows(app, tid)
    if sol_leaders:
        L += ["", "<b>🟣 Solana</b>"]
        with closing(_sol.connect(app)) as conn:
            for r in sol_leaders:
                t = conn.execute("SELECT * FROM trades WHERE wallet=? ORDER BY sell_ts DESC LIMIT 1", (r["wallet"],)).fetchone()
                L.append(f"   {_sol_link(r['wallet'])}")
                if t:
                    hold = int(t["hold_seconds"] or 0) / 60
                    L.append(f"      last closed cycle {_sol_tx_link(app, t['buy_signature'])} → {_sol_tx_link(app, t['sell_signature'])} • net {Decimal(str(t['net_sol'])):+.6f} SOL • hold {hold:.1f}m")
                else:
                    L.append("      ⏳ history study pending")
    return "\n".join(L)


# closing is used by last_entries_page without importing the large Solana module internals elsewhere.
from contextlib import closing


def _answer(app, cb, text=""):
    cqid = (cb or {}).get("id")
    if cqid:
        try:
            _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
        except Exception:
            pass


def _render(app, tid, cb, text, keyboard):
    try:
        _tg._render(app, tid, text, keyboard, cb)
    except Exception:
        _ui._send(app, tid, text, keyboard)


def _refresh_sol_worker(app, tid, key):
    try:
        _sol.request_refresh(app)
        _sol.refresh_rankings(app, tid)
        _ui._send(app, tid, "✅ <b>Solana refresh queued</b>\nStored rankings rebuilt; finalized-block discovery and bounded history backfill continue automatically.", solana_keyboard())
    except Exception as exc:
        _ui._send(app, tid, f"❌ Solana refresh failed\n<code>{html.escape(str(exc)[:300])}</code>", solana_keyboard())
    finally:
        with _LOCK:
            _BUSY.discard((str(tid), key))


def _start_sol_refresh(app, tid):
    key = "sol-refresh"
    token = (str(tid), key)
    with _LOCK:
        if token in _BUSY:
            return
        _BUSY.add(token)
    _ui._send(app, tid, "⏳ <b>Solana</b> refresh queued…")
    threading.Thread(target=_refresh_sol_worker, args=(app, tid, key), daemon=True, name=f"sol-refresh-{tid}").start()


def handle_update(app, update):
    _APP_HOLDER["app"] = app
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data.startswith("sibot:solana") or data in {"sibot:intel", "sibot:lastentries", "sibot:top20:solana", "sibot:leaders:solana"} or data.startswith("sibot:profile:"):
            if not _ui._auth(app, tid):
                _answer(app, cb, "Not authorised")
                return
            _answer(app, cb)
            try:
                if data == "sibot:solana":
                    _render(app, tid, cb, solana_page(app, tid), solana_keyboard())
                elif data in {"sibot:solana:top20", "sibot:top20:solana"}:
                    _render(app, tid, cb, solana_top20_page(app, tid), solana_keyboard())
                elif data in {"sibot:solana:leaders", "sibot:leaders:solana"}:
                    _render(app, tid, cb, solana_leaders_page(app, tid), solana_keyboard())
                elif data == "sibot:solana:positions":
                    _render(app, tid, cb, solana_positions_page(app, tid), solana_keyboard())
                elif data == "sibot:solana:refresh":
                    _start_sol_refresh(app, tid)
                elif data == "sibot:intel":
                    _render(app, tid, cb, intelligence_page(app, tid), _profile_buttons(app, tid))
                elif data == "sibot:lastentries":
                    _render(app, tid, cb, last_entries_page(app, tid), sibot_keyboard(app, tid))
                elif data.startswith("sibot:profile:"):
                    wallet = data.split(":", 2)[2]
                    _render(app, tid, cb, profile_page(app, tid, wallet), _profile_buttons(app, tid))
            except Exception as exc:
                _render(app, tid, cb, f"❌ <b>SiBot intelligence</b>\n<code>{html.escape(str(exc)[:360])}</code>", sibot_keyboard(app, tid))
            return

    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        parts = text.split()
        cmd = parts[0].split("@", 1)[0].lower()
        if cmd in {"/sibotsolana", "/sibotintel", "/sibotlast", "/sibotprofile"}:
            if not _ui._auth(app, tid):
                return
            try:
                if cmd == "/sibotsolana":
                    _ui._send(app, tid, solana_page(app, tid), solana_keyboard())
                elif cmd == "/sibotintel":
                    _ui._send(app, tid, intelligence_page(app, tid), _profile_buttons(app, tid))
                elif cmd == "/sibotlast":
                    _ui._send(app, tid, last_entries_page(app, tid), sibot_keyboard(app, tid))
                elif cmd == "/sibotprofile":
                    if len(parts) != 2:
                        raise ValueError("Use /sibotprofile 0xADDRESS")
                    _ui._send(app, tid, profile_page(app, tid, parts[1]), _profile_buttons(app, tid))
            except Exception as exc:
                _ui._send(app, tid, f"❌ <b>SiBot intelligence</b>\n<code>{html.escape(str(exc)[:360])}</code>", sibot_keyboard(app, tid))
            return
    return _original_handle_update(app, update)


def start_menu_thread(app):
    _APP_HOLDER["app"] = app
    try:
        _intel.start_workers(app)
    except Exception as exc:
        print("[sibot-intelligence-start]", type(exc).__name__, exc)
    try:
        _sol.start_workers(app)
    except Exception as exc:
        print("[sibot-solana-start]", type(exc).__name__, exc)
    return _original_start_menu_thread(app)


def install():
    if getattr(_ui, "_sibot_intelligence_ui_installed", False):
        return
    _tg.sibot_keyboard = sibot_keyboard
    _tg._chain_picker = _chain_picker
    _tg.top20_summary_page = top20_summary_page
    _tg.main_page = main_page
    _tg.help_page = help_page
    _ui.handle_update = handle_update
    _ui.start_menu_thread = start_menu_thread
    _ui._sibot_intelligence_ui_installed = True


install()
