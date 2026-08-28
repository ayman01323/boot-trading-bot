from __future__ import annotations

import copy
import html
import time
from contextlib import closing

from . import solana_sibot as _sol
from . import telegram_ui as _ui
from .solana_live_patch import live_enabled
from .solana_wallet_store import SolanaWalletStore


# Presentation-only integration layer. It does not arm/disarm trading and does not
# submit transactions. Existing EVM pages remain authoritative and are extended
# with the equivalent Solana state/research information.
_PREV_MENU_KEYBOARD = _ui.menu_keyboard
_PREV_HOME_TEXT = _ui.home_text
_PREV_AUTO_PAGE = _ui.auto_page
_PREV_OPPORTUNITIES_PAGE = _ui.opportunities_page
_PREV_PRODUCTS_PAGE = _ui.products_page
_PREV_POWER_PAGE = _ui.power_page
_PREV_TRADING_PAGE = _ui.trading_page
_PREV_STATUS_PAGE = _ui.status_page
_PREV_WALLETS_PAGE = _ui.wallets_page
_PREV_PROFIT_PAGE = _ui.profit_page
_PREV_RANKINGS_PAGE = _ui.rankings_page
_PREV_BEHAVIOURS_PAGE = _ui.behaviours_page
_PREV_COPY20_PAGE = _ui.copy20_page
_PREV_SIGNALS_PAGE = _ui.signals_page
_PREV_STRATEGIES_PAGE = _ui.strategies_page
_PREV_HELP_PAGE = _ui.help_page
_PREV_CONTROL_PAGE = _ui.control_page
_PREV_CONTROL_KEYBOARD = _ui.control_keyboard
_PREV_QUEUE_PAGE = getattr(_ui, "queue_page", None)
_PREV_REPORT = _ui.build_report_html

DIV = "━━━━━━━━━━━━"


def _on(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _short(value, left=8, right=6):
    value = str(value or "")
    if not value:
        return "-"
    return value if len(value) <= left + right + 2 else f"{value[:left]}…{value[-right:]}"


def _append(base: str, lines: list[str]) -> str:
    return (base.rstrip() + "\n\n" + "\n".join(lines)).strip()


def _sol_global(app):
    cfg = _sol.settings(app)
    enabled = _on(cfg.get("enabled"), True)
    try:
        status = _sol.status(app)
    except Exception:
        status = {"candidates": 0, "histories": 0, "closed_trades": 0, "leaders": 0, "open_positions": 0}
    products = 0
    recent = []
    candidates = []
    profit = []
    leaders = []
    try:
        with closing(_sol.connect(app)) as conn:
            products = int(conn.execute(
                "SELECT COUNT(*) n FROM (SELECT mint FROM trades UNION SELECT mint FROM leader_events UNION SELECT mint FROM positions)"
            ).fetchone()["n"])
            recent = [dict(r) for r in conn.execute(
                "SELECT * FROM leader_events WHERE EXISTS (SELECT 1 FROM leaders l WHERE l.wallet=leader_events.leader_wallet) "
                "ORDER BY event_ts DESC LIMIT 8"
            ).fetchall()]
            candidates = [dict(r) for r in conn.execute(
                "SELECT wallet,swap_events,last_seen FROM candidates ORDER BY swap_events DESC,last_seen DESC LIMIT 8"
            ).fetchall()]
            profit = [dict(r) for r in conn.execute(
                "SELECT wallet,SUM(CAST(net_sol AS REAL)) net,COUNT(*) closed,"
                "SUM(CASE WHEN CAST(net_sol AS REAL)>0 THEN 1 ELSE 0 END) wins,AVG(hold_seconds) avg_hold "
                "FROM trades GROUP BY wallet HAVING SUM(CAST(net_sol AS REAL))>0 ORDER BY net DESC LIMIT 8"
            ).fetchall()]
            leaders = [dict(r) for r in conn.execute(
                "SELECT wallet,MIN(rank) best_rank,MAX(CAST(net_profit_sol AS REAL)) net_profit_sol,"
                "MAX(win_rate) win_rate,MAX(closed_trades) closed_trades FROM leaders GROUP BY wallet "
                "ORDER BY net_profit_sol DESC LIMIT 8"
            ).fetchall()]
    except Exception:
        pass
    return {
        "cfg": cfg,
        "enabled": enabled,
        "status": status,
        "products": products,
        "recent": recent,
        "candidates": candidates,
        "profit": profit,
        "leaders": leaders,
    }


def _sol_user(app, tid):
    g = _sol_global(app)
    wallet = None
    signing = False
    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        wallet = store.get_meta(tid)
        signing = store.has_private_key(tid, wallet.get("wallet_id"))
    except Exception:
        wallet = None
    try:
        live = bool(live_enabled(app, tid))
    except Exception:
        live = False
    try:
        leaders = _sol.leader_rows(app, tid)
    except Exception:
        leaders = []
    try:
        positions = _sol.position_rows(app, tid, open_only=True)
    except Exception:
        positions = []
    live_positions = [p for p in positions if str(p.get("mode") or "").upper() == "LIVE"]
    recent = []
    try:
        with closing(_sol.connect(app)) as conn:
            recent = [dict(r) for r in conn.execute(
                "SELECT e.* FROM leader_events e JOIN leaders l ON l.wallet=e.leader_wallet "
                "WHERE l.telegram_id=? ORDER BY e.event_ts DESC LIMIT 6", (str(tid),)
            ).fetchall()]
    except Exception:
        pass
    return {
        **g,
        "wallet": wallet,
        "signing": signing,
        "live": live,
        "leaders_user": leaders,
        "positions": positions,
        "live_positions": live_positions,
        "recent_user": recent,
    }


def menu_keyboard(app=None, chat_id=None):
    kb = copy.deepcopy(_PREV_MENU_KEYBOARD(app, chat_id))
    replacements = {
        "💱 My Live Trading": "💱 Live Trading — All Chains",
        "⚡ My Auto Routes": "⚡ Auto Trade — All Chains",
        "🛰 Opportunities": "🛰 Opportunities — All Chains",
        "🧺 Auto Products": "🧺 Products — All Chains",
        "🔥 Full Power": "🔥 Full Power — All Chains",
        "📡 Status": "📡 Status — All Chains",
        "👥 Copy Top 20": "👥 Copy Top 20 — EVM + SOL",
        "🚦 IN / OUT": "🚦 Signals — EVM + SOL",
        "🤖 Observed Wallets": "🤖 Observed Wallets — All",
        "💰 Wallet Profit": "💰 Wallet Profit — All",
        "🏆 Highest & Fastest": "🏆 Highest & Fastest — All",
        "🔬 Trade Behaviours": "🔬 Behaviours — EVM + SOL",
        "🧠 Strategies": "🧠 Strategies — All",
        "📥 Execution Queue": "📥 Execution / LIVE State",
        "📊 Full Report": "📊 Full Report — All Chains",
    }
    for row in kb.get("inline_keyboard", []):
        for b in row:
            if b.get("text") in replacements:
                b["text"] = replacements[b["text"]]
    return kb


def home_text():
    base = _PREV_HOME_TEXT()
    return base.replace(
        "on every enabled EVM chain.",
        "on every enabled EVM chain, while Solana uses finalized-block wallet research plus Jupiter-backed leader following."
    ) + "\n\n<b>All-chain UI:</b> Ethereum, BSC, Polygon, Base, Arbitrum and Solana are shown throughout the trading, auto, opportunity, product, research and status pages."


def auto_page(app, chat_id):
    base = _PREV_AUTO_PAGE(app, chat_id)
    s = _sol_user(app, chat_id)
    cfg = s["cfg"]
    wallet = s["wallet"] or {}
    lines = [
        "<b>🟣 SOLANA AUTO TRADE</b>", DIV,
        f"Engine: <b>{'ACTIVE' if s['enabled'] else 'INACTIVE'}</b>",
        f"LIVE auto: <b>{'🟢 ARMED' if s['live'] else '🔴 OFF'}</b>",
        f"Signing wallet: <b>{'READY' if s['signing'] else 'NOT READY'}</b> <code>{html.escape(_short(wallet.get('address')))}</code>",
        f"Trade size: <b>{html.escape(str(cfg.get('live_trade_sol', '0.009')))} SOL</b>",
        f"Untouched SOL reserve: <b>{html.escape(str(cfg.get('live_min_sol_reserve', '0.02')))} SOL</b>",
        f"Max LIVE positions: <b>{html.escape(str(cfg.get('live_max_positions', '1')))}</b>",
        f"Selected leaders: <b>{len(s['leaders_user'])}</b> | Open LIVE positions: <b>{len(s['live_positions'])}</b>",
        "Execution: <b>fresh leader BUY → Jupiter validation → signed simulation → Jupiter execute</b>.",
        "Open <b>🤖 SiBot → 🟣 Solana</b> to enable/disable Solana LIVE.",
    ]
    return _append(base, lines)


def trading_page(app, chat_id):
    base = _PREV_TRADING_PAGE(app, chat_id)
    s = _sol_user(app, chat_id)
    cfg = s["cfg"]
    wallet = s["wallet"] or {}
    lines = [
        "<b>🟣 SOLANA LIVE TRADING</b>", DIV,
        f"Solana: <b>{'ACTIVE' if s['enabled'] else 'INACTIVE'}</b> | LIVE: <b>{'🟢 ARMED' if s['live'] else '🔴 OFF'}</b>",
        f"Active Solana wallet: <code>{html.escape(_short(wallet.get('address')))}</code> | <b>{'SIGNING READY' if s['signing'] else 'PUBLIC ONLY / MISSING'}</b>",
        f"Automatic LIVE size: <b>{html.escape(str(cfg.get('live_trade_sol', '0.009')))} SOL</b> | reserve: <b>{html.escape(str(cfg.get('live_min_sol_reserve', '0.02')))} SOL</b>",
        "Venue: <b>Jupiter Swap V2</b>; every automatic LIVE transaction must pass signed Solana simulation before execution.",
        "Solana manual EVM-style <code>/buy</code>/<code>/sell</code> commands are not reused; Solana LIVE is controlled from the dedicated Solana SiBot page.",
    ]
    return _append(base, lines)


def opportunities_page(app, chat_id):
    base = _PREV_OPPORTUNITIES_PAGE(app, chat_id)
    s = _sol_user(app, chat_id)
    lines = ["<b>🟣 SOLANA FRESH LEADER SIGNALS</b>", DIV]
    if not s["recent_user"]:
        lines.append("No recent selected-leader Solana signals yet.")
    else:
        now = int(time.time())
        for r in s["recent_user"]:
            age = max(0, now - int(r.get("event_ts") or now))
            action = str(r.get("action") or "?").upper()
            icon = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"
            lines.append(
                f"{icon} <b>{html.escape(action)}</b> age {age}s | leader <code>{html.escape(_short(r.get('leader_wallet')))}</code> | "
                f"mint <code>{html.escape(_short(r.get('mint')))}</code> | {html.escape(str(r.get('sol_amount') or '0'))} SOL"
            )
        lines.append("A Solana signal is not automatically a trade: freshness, Jupiter round-trip, entry deterioration, position limit, signer and reserve checks still apply.")
    return _append(base, lines)


def products_page(app, chat_id):
    base = _PREV_PRODUCTS_PAGE(app, chat_id)
    s = _sol_user(app, chat_id)
    lines = [
        "<b>🟣 SOLANA / SPL PRODUCT UNIVERSE</b>", DIV,
        f"Observed distinct SPL mints: <b>{s['products']}</b>",
        f"Wallet candidates: <b>{s['status']['candidates']}</b> | reconstructed closed trades: <b>{s['status']['closed_trades']}</b>",
        "Solana does not use the EVM <code>tokens.csv</code> product tiers. Its product set is dynamic from leader/history discovery and every LIVE entry is accepted or rejected at signal time by Jupiter quote, round-trip-loss and deterioration checks.",
    ]
    return _append(base, lines)


def power_page(app, chat_id):
    base = _PREV_POWER_PAGE(app, chat_id)
    s = _sol_user(app, chat_id)
    st = s["status"]
    lines = [
        "<b>🟣 SOLANA FULL POWER</b>", DIV,
        f"Scanner/research: <b>{'ON' if s['enabled'] else 'OFF'}</b> | LIVE: <b>{'ARMED' if s['live'] else 'OFF'}</b> | signer: <b>{'READY' if s['signing'] else 'NOT READY'}</b>",
        f"Candidates: <b>{st['candidates']}</b> | histories with closed trades: <b>{st['histories']}</b> | closed trades: <b>{st['closed_trades']}</b>",
        f"Selected leader wallets: <b>{len(s['leaders_user'])}</b> | open LIVE positions: <b>{len(s['live_positions'])}</b>",
        "Path: finalized Solana discovery + realised-SOL ranking + fresh leader monitoring + Jupiter LIVE execution.",
    ]
    return _append(base, lines)


def status_page(app):
    base = _PREV_STATUS_PAGE(app)
    s = _sol_global(app)
    st = s["status"]
    cfg = s["cfg"]
    lines = [
        "<b>🟣 Solana</b>",
        f"Engine: <b>{'ACTIVE' if s['enabled'] else 'INACTIVE'}</b> | RPC: <b>{'configured' if cfg.get('rpc_url') else 'missing'}</b> | base <b>SOL</b>",
        f"Candidates {st['candidates']:,} | histories {st['histories']:,} | closed trades {st['closed_trades']:,} | leaders {st['leaders']:,} | open positions {st['open_positions']:,}",
    ]
    return _append(base, lines)


def wallets_page(app):
    base = _PREV_WALLETS_PAGE(app)
    s = _sol_global(app)
    lines = ["<b>🟣 SOLANA OBSERVED WALLETS</b>", DIV]
    if not s["candidates"]:
        lines.append("No Solana swap-wallet candidates yet.")
    for r in s["candidates"]:
        lines.append(f"• <code>{html.escape(_short(r.get('wallet')))}</code> — observed swap events <b>{int(r.get('swap_events') or 0)}</b>")
    lines.append("Solana candidate status means observed swap activity; profitability is established separately from reconstructed closed SOL round trips.")
    return _append(base, lines)


def profit_page(app):
    base = _PREV_PROFIT_PAGE(app)
    s = _sol_global(app)
    lines = ["<b>🟣 SOLANA WALLET PROFIT RESEARCH</b>", DIV]
    if not s["profit"]:
        lines.append("No positive reconstructed Solana closed-trade wallet P&L yet.")
    for r in s["profit"]:
        closed = max(1, int(r.get("closed") or 0))
        wins = int(r.get("wins") or 0)
        lines.append(
            f"• <code>{html.escape(_short(r.get('wallet')))}</code> — net <b>{float(r.get('net') or 0):+.6f} SOL</b> | "
            f"positive {wins/closed*100:.1f}% | closed {closed}"
        )
    return _append(base, lines)


def rankings_page(app):
    base = _PREV_RANKINGS_PAGE(app)
    s = _sol_global(app)
    lines = ["<b>🟣 SOLANA HIGHEST & FASTEST</b>", DIV]
    if not s["profit"]:
        lines.append("No positive reconstructed Solana ranking yet.")
    for i, r in enumerate(s["profit"][:8], 1):
        avg_hold = float(r.get("avg_hold") or 0)
        lines.append(
            f"#{i} <code>{html.escape(_short(r.get('wallet')))}</code> — net <b>{float(r.get('net') or 0):+.6f} SOL</b> | "
            f"avg closed-trade hold {avg_hold/60:.1f} min"
        )
    lines.append("Solana speed uses reconstructed closed-trade holding time and is historical research, not a forecast.")
    return _append(base, lines)


def behaviours_page(app):
    base = _PREV_BEHAVIOURS_PAGE(app)
    s = _sol_global(app)
    lines = [
        "<b>🟣 SOLANA BEHAVIOUR RESEARCH</b>", DIV,
        "• <b>Swap-active wallet discovery</b> — finalized Solana blocks identify swap-like signers.",
        "• <b>Realised SOL round-trip reconstruction</b> — ranks wallets from matched token entry/exit cycles.",
        "• <b>Fresh leader following</b> — selected profitable leaders are monitored for new BUY/SELL events.",
        f"Current evidence: candidates <b>{s['status']['candidates']}</b> | closed trades <b>{s['status']['closed_trades']}</b> | leader wallets <b>{s['status']['leaders']}</b>.",
        "The EVM behaviour labels are not silently applied to Solana because the evidence model is different.",
    ]
    return _append(base, lines)


def copy20_page(app):
    base = _PREV_COPY20_PAGE(app)
    s = _sol_global(app)
    lines = ["<b>🟣 SOLANA APPROVED LEADERS</b>", DIV]
    if not s["leaders"]:
        lines.append("No Solana leader currently passes the closed-trade/win-rate selection gates.")
    for r in s["leaders"]:
        lines.append(
            f"• rank ≤{int(r.get('best_rank') or 0)} <code>{html.escape(_short(r.get('wallet')))}</code> — "
            f"net <b>{float(r.get('net_profit_sol') or 0):+.6f} SOL</b> | win {float(r.get('win_rate') or 0):.1f}% | closed {int(r.get('closed_trades') or 0)}"
        )
    return _append(base, lines)


def signals_page(app):
    base = _PREV_SIGNALS_PAGE(app)
    s = _sol_global(app)
    lines = ["<b>🟣 SOLANA LEADER IN / OUT SIGNALS</b>", DIV]
    if not s["recent"]:
        lines.append("No recent Solana selected-leader events.")
    else:
        now = int(time.time())
        for r in s["recent"][:8]:
            age = max(0, now - int(r.get("event_ts") or now))
            action = str(r.get("action") or "?").upper()
            lines.append(
                f"{'🟢' if action=='BUY' else '🔴' if action=='SELL' else '⚪'} <b>{html.escape(action)}</b> age {age}s | "
                f"leader <code>{html.escape(_short(r.get('leader_wallet')))}</code> | mint <code>{html.escape(_short(r.get('mint')))}</code>"
            )
    return _append(base, lines)


def strategies_page(app):
    text, kb = _PREV_STRATEGIES_PAGE(app)
    s = _sol_global(app)
    extra = [
        "<b>🟣 Solana</b>",
        "• <b>Profitable-leader copy strategy</b> — realised-SOL Top-20 → leader reliability gates → fresh BUY/SELL monitoring → Jupiter validation/execution.",
        f"Candidates {s['status']['candidates']} | closed trades {s['status']['closed_trades']} | leaders {s['status']['leaders']}",
    ]
    out = copy.deepcopy(kb)
    rows = out.setdefault("inline_keyboard", [])
    insert_at = max(0, len(rows) - 1)
    rows.insert(insert_at, [{"text": "🟣 Solana Strategy / LIVE", "callback_data": "sibot:solana"}])
    return _append(text, extra), out


def help_page():
    base = _PREV_HELP_PAGE()
    return _append(base, [
        "<b>🟣 SOLANA</b>",
        "Solana uses its own public-key/signing wallet, finalized-block swap discovery, realised SOL P&L reconstruction and leader selection.",
        "For LIVE automatic entries the bot uses Jupiter, requires a fresh qualifying leader signal, validates round-trip loss and entry deterioration, signs with the encrypted active Solana key, simulates the signed transaction and only then executes.",
        "Use <code>/sibottop20 solana</code>, <code>/sibotleaders solana</code> and <code>/solwallet</code>. The dedicated Solana SiBot page controls Solana LIVE.",
    ])


def control_page(app):
    base = _PREV_CONTROL_PAGE(app)
    s = _sol_global(app)
    cfg = s["cfg"]
    return _append(base, [
        "<b>🟣 Solana controls</b>",
        f"Research engine: <b>{'ON' if s['enabled'] else 'OFF'}</b> | trade size <b>{html.escape(str(cfg.get('live_trade_sol','0.009')))} SOL</b> | reserve <b>{html.escape(str(cfg.get('live_min_sol_reserve','0.02')))} SOL</b>",
        "Solana per-user LIVE arming is intentionally controlled on the dedicated Solana page rather than by the EVM chain-toggle buttons.",
    ])


def control_keyboard(app):
    kb = copy.deepcopy(_PREV_CONTROL_KEYBOARD(app))
    rows = kb.setdefault("inline_keyboard", [])
    insert_at = max(0, len(rows) - 1)
    rows.insert(insert_at, [{"text": "🟣 Solana LIVE / AUTO", "callback_data": "sibot:solana"}])
    return kb


def queue_page(app):
    if _PREV_QUEUE_PAGE is None:
        return "<b>📥 EXECUTION / LIVE STATE</b>"
    base = _PREV_QUEUE_PAGE(app)
    s = _sol_global(app)
    return _append(base, [
        "<b>🟣 Solana execution</b>",
        f"Open Solana positions: <b>{s['status']['open_positions']}</b> | selected leader wallets: <b>{s['status']['leaders']}</b>.",
        "Solana does not use the EVM local route queue: qualifying Solana LIVE signals follow the guarded Jupiter order → sign → simulate → execute path and positions are tracked in the Solana SiBot database.",
    ])


def build_report_html(app):
    base = _PREV_REPORT(app)
    s = _sol_global(app)
    st = s["status"]
    return _append(base, [
        "<b>🟣 SOLANA REPORT</b>", DIV,
        f"Engine {'ON' if s['enabled'] else 'OFF'} | candidates <b>{st['candidates']}</b> | histories <b>{st['histories']}</b> | closed trades <b>{st['closed_trades']}</b> | leaders <b>{st['leaders']}</b> | open positions <b>{st['open_positions']}</b>",
        f"Observed SPL mints: <b>{s['products']}</b> | execution venue: <b>Jupiter</b>.",
    ])


def install():
    _ui.menu_keyboard = menu_keyboard
    _ui.home_text = home_text
    _ui.auto_page = auto_page
    _ui.opportunities_page = opportunities_page
    _ui.products_page = products_page
    _ui.power_page = power_page
    _ui.trading_page = trading_page
    _ui.status_page = status_page
    _ui.wallets_page = wallets_page
    _ui.profit_page = profit_page
    _ui.rankings_page = rankings_page
    _ui.behaviours_page = behaviours_page
    _ui.copy20_page = copy20_page
    _ui.signals_page = signals_page
    _ui.strategies_page = strategies_page
    _ui.help_page = help_page
    _ui.control_page = control_page
    _ui.control_keyboard = control_keyboard
    _ui.queue_page = queue_page
    _ui.build_report_html = build_report_html


install()
