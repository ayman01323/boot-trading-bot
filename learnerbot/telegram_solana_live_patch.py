from __future__ import annotations

import html
from decimal import Decimal

from . import solana_sibot as _sol
from . import telegram_sibot_intelligence_patch as _intel
from . import telegram_ui as _ui
from .solana_execution_fault_counter_patch import reset_fault_count
from .solana_live_patch import live_enabled
from .solana_wallet_store import SolanaWalletStore
from .telegram import answer_callback_query, send_message
from .user_registry import require_user, set_user_setting

_PREV_HANDLE = _ui.handle_update
DIV = _intel.DIV


def _store(app):
    return SolanaWalletStore(app.csv_dir, app.data_dir)


def _balance(app, address):
    try:
        result = _sol._rpc(app, "getBalance", [str(address), {"commitment": "confirmed"}]) or {}
        return Decimal(int(result.get("value") or 0)) / Decimal(1_000_000_000)
    except Exception:
        return None


def solana_page(app, tid):
    cfg = _sol.settings(app)
    s = _sol.status(app)
    rows = _sol.ranking_rows(app, tid)
    leaders = _sol.leader_rows(app, tid)
    positions = [p for p in _sol.position_rows(app, tid, open_only=True) if str(p.get("mode") or "").upper() == "LIVE"]
    enabled = live_enabled(app, tid)
    try:
        store = _store(app)
        meta = store.get_meta(tid)
        signing = store.has_private_key(tid, meta.get("wallet_id"))
        address = str(meta.get("address") or "")
        bal = _balance(app, address)
        wallet_line = f"🔐 Active wallet  <code>{html.escape(address[:8] + '…' + address[-6:])}</code> • {'SIGNING READY' if signing else 'PUBLIC ONLY'}"
        balance_line = f"💰 Balance  <b>{bal:.9f} SOL</b>" if bal is not None else "💰 Balance  <b>unavailable</b>"
    except Exception:
        wallet_line = "🔐 Active wallet  <b>not configured</b>"
        balance_line = "💰 Balance  <b>unavailable</b>"
    return "\n".join([
        "<b>🟣 SiBot — Solana LIVE</b>", DIV,
        f"{'🟢 <b>LIVE ARMED</b>' if enabled else '🔴 <b>LIVE OFF</b>'}  •  no new SHADOW entries",
        wallet_line,
        balance_line,
        "",
        f"🚀 Trade size  <b>{html.escape(str(cfg.get('live_trade_sol','.005')))} SOL</b>",
        f"🛡 Untouched reserve  <b>{html.escape(str(cfg.get('live_min_sol_reserve','.02')))} SOL</b>",
        f"1️⃣ Max LIVE positions  <b>{html.escape(str(cfg.get('live_max_positions','1')))}</b>",
        "🧪 Signed transaction simulation  <b>REQUIRED</b>",
        "🧯 Landed-invalid circuit breaker  <b>2 faults → LIVE OFF</b>",
        "",
        f"👀 Candidates  <b>{s['candidates']}</b> • 📈 Top-20  <b>{len(rows)}</b> • 🏆 Leaders  <b>{len(leaders)}</b>",
        f"💼 Open LIVE positions  <b>{len(positions)}</b>",
        "",
        "<i>When armed, the next qualifying fresh leader BUY can spend real SOL. Existing historical SHADOW rows are not used for new entries.</i>",
    ])


def solana_keyboard(app, tid):
    enabled = live_enabled(app, tid)
    live_button = {"text": "🛑 Disable LIVE", "callback_data": "sibot:solana:live:off"} if enabled else {"text": "🚀 Enable LIVE", "callback_data": "sibot:solana:live:arm"}
    return {"inline_keyboard": [
        [live_button],
        [{"text": "📈 Top 20", "callback_data": "sibot:solana:top20"}, {"text": "🏆 Leaders", "callback_data": "sibot:solana:leaders"}],
        [{"text": "💼 LIVE Positions", "callback_data": "sibot:solana:positions"}, {"text": "🔄 Refresh", "callback_data": "sibot:solana:refresh"}],
        [{"text": "⬅️ SiBot", "callback_data": "menu:sibot"}],
    ]}


def solana_positions_page(app, tid):
    rows = [p for p in _sol.position_rows(app, tid, open_only=True) if str(p.get("mode") or "").upper() == "LIVE"]
    L = ["<b>💼 Solana SiBot LIVE Positions</b>", DIV]
    if not rows:
        return "\n".join(L + ["", "✅ No open Solana LIVE positions."])
    for p in rows[:20]:
        pct = Decimal(str(p.get("unrealised_pct") or 0))
        pnl = Decimal(str(p.get("unrealised_net_sol") or 0))
        icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        mint = str(p.get("mint") or "")
        L += ["", f"🪙 <code>{html.escape(mint[:8] + '…' + mint[-6:])}</code>", f"{icon} Estimated P&L <b>{pnl:+.6f} SOL</b> ({pct:+.2f}%)", f"Leader: <code>{html.escape(str(p.get('leader_wallet') or '')[:16])}…</code>"]
    return "\n".join(L)


def _chat(update):
    q = update.get("callback_query") or {}
    m = q.get("message") or {}
    c = m.get("chat") or {}
    return c.get("type") == "private", str(c.get("id") or ""), str(q.get("id") or "")


def handle_update(app, update):
    q = update.get("callback_query") or {}
    data = str(q.get("data") or "")
    private, tid, qid = _chat(update)

    if data in {"sibot:solana", "sibot:solana:refresh", "sibot:solana:positions"} and tid:
        if qid:
            try:
                answer_callback_query(app.telegram_bot_token, qid, "Refreshed" if data.endswith("refresh") else "")
            except Exception:
                pass
        text = solana_positions_page(app, tid) if data.endswith("positions") else solana_page(app, tid)
        send_message(app.telegram_bot_token, tid, text, parse_mode="HTML", reply_markup=solana_keyboard(app, tid))
        return True

    if data not in {"sibot:solana:live:arm", "sibot:solana:live:confirm", "sibot:solana:live:off"}:
        return _PREV_HANDLE(app, update)
    if not private or not tid:
        if qid:
            answer_callback_query(app.telegram_bot_token, qid, "Solana LIVE can only be changed in a private chat.")
        return True
    try:
        user = require_user(app.csv_dir, tid, active=True, chain_slug="solana")
        if str(user.get("can_auto_trade") or "false").lower() not in {"1", "true", "yes", "on"}:
            raise ValueError("This user is not permitted to use automatic trading")
        if data == "sibot:solana:live:off":
            set_user_setting(app.csv_dir, tid, "solana_live_enabled", "false", chain_id=str(_sol.SOLANA_CHAIN_ID), description="Solana real-money auto execution")
            answer_callback_query(app.telegram_bot_token, qid, "Solana LIVE disabled")
            send_message(app.telegram_bot_token, tid, solana_page(app, tid), parse_mode="HTML", reply_markup=solana_keyboard(app, tid))
            return True
        store = _store(app)
        meta = store.get_meta(tid)
        if not store.has_private_key(tid, meta.get("wallet_id")):
            raise ValueError("Active Solana wallet is not SIGNING READY. Import its private key first.")
        cfg = _sol.settings(app)
        trade = min(Decimal("0.005"), max(Decimal("0.0005"), Decimal(str(cfg.get("live_trade_sol") or ".005"))))
        reserve = max(Decimal("0.01"), Decimal(str(cfg.get("live_min_sol_reserve") or ".02")))
        bal = _balance(app, meta.get("address"))
        if bal is None or bal < trade + reserve:
            raise ValueError(f"Need at least {trade + reserve:.6f} SOL in the active wallet before LIVE can be armed")
        if data == "sibot:solana:live:arm":
            answer_callback_query(app.telegram_bot_token, qid, "Review and confirm LIVE trading")
            text = "\n".join([
                "<b>⚠️ CONFIRM SOLANA LIVE TRADING</b>", DIV,
                "This enables real-money automatic swaps from the active Solana signing wallet.",
                f"First-trade cap: <b>{trade} SOL</b>",
                f"Untouched reserve: <b>{reserve} SOL</b>",
                "Max simultaneous LIVE positions: <b>1</b>",
                "Every transaction must pass signed Solana simulation before Jupiter execution.",
                "A landed transaction with no valid economic output counts as a safety fault; two faults automatically disable LIVE.",
                "",
                "Press <b>CONFIRM LIVE</b> only after investigating any previous automatic safety shutdown.",
            ])
            kb = {"inline_keyboard": [[{"text": "🚀 CONFIRM LIVE", "callback_data": "sibot:solana:live:confirm"}], [{"text": "Cancel", "callback_data": "sibot:solana"}]]}
            send_message(app.telegram_bot_token, tid, text, parse_mode="HTML", reply_markup=kb, protect_content=True)
            return True
        # Only an explicit private-chat CONFIRM clears the persistent landed-fault
        # counter. Service restarts and one-shot startup migrations do not clear it.
        reset_fault_count(app, tid)
        set_user_setting(app.csv_dir, tid, "sibot_enabled", "true", chain_id="*", description="SiBot monitoring enabled")
        set_user_setting(app.csv_dir, tid, "solana_live_enabled", "true", chain_id=str(_sol.SOLANA_CHAIN_ID), description="Solana real-money auto execution")
        answer_callback_query(app.telegram_bot_token, qid, "Solana LIVE armed")
        send_message(app.telegram_bot_token, tid, "🚀 <b>Solana LIVE is ARMED.</b>\nThe landed-execution fault counter has been reset by your explicit confirmation. The next qualifying fresh leader BUY may execute with real SOL under the safety limits.", parse_mode="HTML", reply_markup=solana_keyboard(app, tid), protect_content=True)
        return True
    except Exception as exc:
        if qid:
            try:
                answer_callback_query(app.telegram_bot_token, qid, str(exc)[:170])
            except Exception:
                pass
        try:
            send_message(app.telegram_bot_token, tid, f"🚨 <b>Solana LIVE not enabled</b>\n<code>{html.escape(str(exc)[:600])}</code>", parse_mode="HTML", protect_content=True)
        except Exception:
            pass
        return True


def install():
    _intel.solana_page = solana_page
    _intel.solana_positions_page = solana_positions_page
    _ui.handle_update = handle_update


install()
