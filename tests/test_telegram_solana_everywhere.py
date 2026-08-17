from decimal import Decimal

from learnerbot import telegram_solana_everywhere_patch as p
from learnerbot import telegram_solana_everywhere_compat_patch as c
from learnerbot import telegram_ui as ui


def _texts(kb):
    return [b.get("text", "") for row in kb.get("inline_keyboard", []) for b in row]


def test_user_menu_labels_are_all_chain_aware():
    texts = _texts(ui.menu_keyboard())
    assert "🤖 SiBot — EVM + SOL" in texts
    assert "💰 Capital & P&L — All" in texts
    assert "🔐 Wallets — EVM + SOL" in texts
    assert "💱 Trading — All Chains" in texts
    assert "⚡ Auto Trade — All Chains" in texts
    assert "🛰 Opportunities — All Chains" in texts
    assert "🧺 Products — All Chains" in texts
    assert "🔥 Full Power — All Chains" in texts
    assert "📡 Status — All Chains" in texts


def test_master_aliases_are_all_chain_aware(monkeypatch):
    monkeypatch.setattr(c, "_PREV_MENU", lambda app=None, chat_id=None: {"inline_keyboard": [
        [{"text": "👥 Copy Top 20", "callback_data": "menu:copy20"}, {"text": "🚦 IN / OUT", "callback_data": "menu:signals"}],
        [{"text": "💰 Profit Research", "callback_data": "menu:profit"}, {"text": "🏆 Rankings", "callback_data": "menu:rankings"}],
        [{"text": "🔬 Behaviours", "callback_data": "menu:behaviours"}, {"text": "🧠 Strategies", "callback_data": "menu:strategies"}],
        [{"text": "📊 Full Technical Report", "callback_data": "menu:report"}],
    ]})
    texts = _texts(c.menu_keyboard())
    assert "👥 Copy Top 20 — EVM + SOL" in texts
    assert "🚦 Signals — EVM + SOL" in texts
    assert "💰 Profit Research — All" in texts
    assert "🏆 Rankings — All" in texts
    assert "🔬 Behaviours — EVM + SOL" in texts
    assert "🧠 Strategies — All" in texts
    assert "📊 Full Report — All Chains" in texts


def _user_snapshot():
    return {
        "cfg": {"live_trade_sol": "0.005", "live_min_sol_reserve": "0.02", "live_max_positions": "1"},
        "enabled": True,
        "status": {"candidates": 123, "histories": 10, "closed_trades": 20, "leaders": 2, "open_positions": 1},
        "products": 44,
        "recent": [],
        "candidates": [],
        "profit": [],
        "leaders": [],
        "wallet": {"address": "6LspdeZhf6HX7YdMuaz3gVUdXW9ifs15cyRTdo3aS3Xr"},
        "signing": True,
        "live": True,
        "leaders_user": [{"wallet": "leader1"}, {"wallet": "leader2"}],
        "positions": [{"mode": "LIVE"}],
        "live_positions": [{"mode": "LIVE"}],
        "recent_user": [{
            "action": "BUY", "event_ts": 1, "leader_wallet": "Leader11111111111111111111111111111111",
            "mint": "Mint1111111111111111111111111111111111", "sol_amount": "0.01"
        }],
    }


def test_user_pages_append_solana(monkeypatch):
    snap = _user_snapshot()
    monkeypatch.setattr(p, "_sol_user", lambda app, tid: snap)
    monkeypatch.setattr(p, "_PREV_AUTO_PAGE", lambda app, tid: "EVM AUTO")
    monkeypatch.setattr(p, "_PREV_TRADING_PAGE", lambda app, tid: "EVM TRADING")
    monkeypatch.setattr(p, "_PREV_OPPORTUNITIES_PAGE", lambda app, tid: "EVM OPP")
    monkeypatch.setattr(p, "_PREV_PRODUCTS_PAGE", lambda app, tid: "EVM PRODUCTS")
    monkeypatch.setattr(p, "_PREV_POWER_PAGE", lambda app, tid: "EVM POWER")

    assert "SOLANA AUTO TRADE" in p.auto_page(None, "1")
    assert "SOLANA LIVE TRADING" in p.trading_page(None, "1")
    assert "SOLANA FRESH LEADER SIGNALS" in p.opportunities_page(None, "1")
    assert "SOLANA / SPL PRODUCT UNIVERSE" in p.products_page(None, "1")
    assert "SOLANA FULL POWER" in p.power_page(None, "1")


def test_global_research_pages_append_solana(monkeypatch):
    snap = _user_snapshot()
    snap.update({
        "profit": [{"wallet": "W", "net": 1.25, "closed": 4, "wins": 3, "avg_hold": 120}],
        "leaders": [{"wallet": "W", "best_rank": 1, "net_profit_sol": 1.25, "win_rate": 75, "closed_trades": 4}],
        "candidates": [{"wallet": "W", "swap_events": 99}],
        "recent": [{"action": "SELL", "event_ts": 1, "leader_wallet": "W", "mint": "M"}],
    })
    monkeypatch.setattr(p, "_sol_global", lambda app: snap)
    monkeypatch.setattr(p, "_PREV_STATUS_PAGE", lambda app: "EVM STATUS")
    monkeypatch.setattr(p, "_PREV_WALLETS_PAGE", lambda app: "EVM WALLETS")
    monkeypatch.setattr(p, "_PREV_PROFIT_PAGE", lambda app: "EVM PROFIT")
    monkeypatch.setattr(p, "_PREV_RANKINGS_PAGE", lambda app: "EVM RANK")
    monkeypatch.setattr(p, "_PREV_BEHAVIOURS_PAGE", lambda app: "EVM BEHAV")
    monkeypatch.setattr(p, "_PREV_COPY20_PAGE", lambda app: "EVM COPY")
    monkeypatch.setattr(p, "_PREV_SIGNALS_PAGE", lambda app: "EVM SIGNALS")
    monkeypatch.setattr(p, "_PREV_REPORT", lambda app: "EVM REPORT")

    assert "Solana" in p.status_page(None)
    assert "SOLANA OBSERVED WALLETS" in p.wallets_page(None)
    assert "SOLANA WALLET PROFIT RESEARCH" in p.profit_page(None)
    assert "SOLANA HIGHEST & FASTEST" in p.rankings_page(None)
    assert "SOLANA BEHAVIOUR RESEARCH" in p.behaviours_page(None)
    assert "SOLANA APPROVED LEADERS" in p.copy20_page(None)
    assert "SOLANA LEADER IN / OUT SIGNALS" in p.signals_page(None)
    assert "SOLANA REPORT" in p.build_report_html(None)


def test_control_keyboard_has_direct_solana_entry(monkeypatch):
    monkeypatch.setattr(p, "_PREV_CONTROL_KEYBOARD", lambda app: {"inline_keyboard": [[{"text": "⬅️ Menu", "callback_data": "menu:home"}]]})
    assert "🟣 Solana LIVE / AUTO" in _texts(p.control_keyboard(None))


def test_capital_dashboard_appends_solana(monkeypatch):
    monkeypatch.setattr(c, "_PREV_USER_DASH", lambda app, tid: "EVM CAPITAL")
    monkeypatch.setattr(c, "_sol_capital", lambda app, tid: {
        "address": "6LspdeZhf6HX7YdMuaz3gVUdXW9ifs15cyRTdo3aS3Xr",
        "native": Decimal("0.08"), "open": 1, "token_exit": Decimal("0.01"),
        "realised": Decimal("0.002"), "unrealised": Decimal("0.001"),
        "total_sol": Decimal("0.09"), "usd": Decimal("18"), "price": Decimal("200"), "live": True,
    })
    text = c.user_dashboard_text(None, "1")
    assert "SOLANA CAPITAL" in text
    assert "0.090000000 SOL" in text
    assert "LIVE <b>ARMED</b>" in text
