from types import SimpleNamespace
from decimal import Decimal

from learnerbot import telegram_live_reporting_patch as p


def test_hourly_includes_solana_wallet_capital_live_and_evm_state(monkeypatch):
    chain = SimpleNamespace(chain_id=1, name="Ethereum", native_symbol="ETH")
    monkeypatch.setattr(p, "load_chains", lambda app, enabled_only=True: [chain])
    monkeypatch.setattr(p._hourly, "user_dashboard_data", lambda app, tid: {
        "capital_usd": Decimal("100"),
        "wallets": [{
            "active": "true",
            "address": "0x1111111111111111111111111111111111111111",
            "chains": [{"chain_id": 1, "native_balance": Decimal("0.1"), "capital_usd": Decimal("100"), "trading_state": "LIVE"}],
        }],
    })
    monkeypatch.setattr(p._hourly, "_reserve_for", lambda app, tid, c: Decimal("0.01"))
    monkeypatch.setattr(p._hourly, "_min_trade_for", lambda app, tid, c: Decimal("0.001"))
    monkeypatch.setattr(p._sibot, "ranking_rows", lambda app, tid, cid: [{"wallet": "w"}])
    monkeypatch.setattr(p, "_evm_sibot_state", lambda app, tid, c: ("🟢 LIVE AUTO", ""))
    monkeypatch.setattr(p, "_sol_snapshot", lambda app, tid: {
        "address": "5sdV3Rr2CV5uLZozZWwtpTSad31Hvi9FniYUAHCfEqbw",
        "native": Decimal("0.08"),
        "open": 1,
        "total_sol": Decimal("0.09"),
        "usd": Decimal("18"),
        "live": True,
        "signing": True,
        "leaders": [{"wallet": "solleader"}],
        "rankings": [{"wallet": "solrank"}],
        "cfg": {"live_min_sol_reserve": "0.02", "live_trade_sol": "0.005"},
    })

    text = p.build_hourly_capital_alert(None, "1")
    assert "Total priced capital  <b>$118.00</b>" in text
    assert "Ethereum" in text and "LIVE AUTO" in text
    assert "Wallet trading state  <b>LIVE</b>" in text
    assert "Active Solana wallet" in text
    assert "5sdV3Rr2…HCfEqbw" in text
    assert "LIVE AUTO ARMED" in text
    assert "0.09 SOL" in text
    assert "selected leaders <b>1</b>" in text
    assert "SHADOW only" not in text


def test_sibot_keyboard_labels_evm_and_solana_separately(monkeypatch):
    monkeypatch.setattr(p, "_PREV_SIBOT_KEYBOARD", lambda app, tid: {"inline_keyboard": [
        [{"text": "🧪 SHADOW", "callback_data": "sibot:auto:arm"}],
        [{"text": "🟣 Solana", "callback_data": "sibot:solana"}],
    ]})
    monkeypatch.setattr(p._sibot, "user_settings", lambda app, tid, cid: {"auto_trade_enabled": "false"})
    monkeypatch.setattr(p, "live_enabled", lambda app, tid: True)
    kb = p.sibot_keyboard(None, "1")
    texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "🧪 EVM SHADOW" in texts
    assert "🟣 Solana LIVE" in texts


def test_combined_leaders_page_includes_solana(monkeypatch):
    monkeypatch.setattr(p, "_PREV_LEADERS_PAGE", lambda app, tid, chain=None: "<b>🏆 SiBot Leaders</b>\n\n<b>🌐 Ethereum</b>\n🥇 #1 evm")
    monkeypatch.setattr(p._sol, "leader_rows", lambda app, tid: [{
        "rank": 1,
        "wallet": "5sdV3Rr2CV5uLZozZWwtpTSad31Hvi9FniYUAHCfEqbw",
        "net_profit_sol": "0.12",
        "win_rate": 75,
        "closed_trades": 8,
    }])
    text = p.leaders_page(None, "1")
    assert "Ethereum" in text
    assert "🟣 Solana" in text
    assert "+0.120000 SOL" in text


def test_sibot_report_appends_solana_live_state_and_combined_leaders(monkeypatch):
    monkeypatch.setattr(p, "_PREV_REPORT_TEXT", lambda app, tid: "EVM CAPITAL REPORT")
    monkeypatch.setattr(p._compat, "_sol_user_section", lambda app, tid: ["<b>🟣 SOLANA CAPITAL &amp; P&amp;L</b>", "LIVE ARMED"])
    monkeypatch.setattr(p, "_all_live_state_lines", lambda app, tid: ["<b>🚦 LIVE STATE — ALL CHAINS</b>", "• Ethereum: LIVE AUTO", "• Solana: LIVE AUTO ARMED"])
    monkeypatch.setattr(p, "_combined_leader_lines", lambda app, tid: ["<b>🏆 SELECTED LEADERS — EVM + SOLANA</b>", "• Ethereum: #1 evm", "• Solana: #1 sol"])
    text = p.report_text(None, "1")
    assert "EVM CAPITAL REPORT" in text
    assert "SOLANA CAPITAL" in text
    assert "LIVE STATE — ALL CHAINS" in text
    assert "SELECTED LEADERS — EVM + SOLANA" in text
    assert "• Solana: #1 sol" in text


def test_help_no_longer_claims_solana_shadow_only(monkeypatch):
    monkeypatch.setattr(p, "_PREV_HELP_PAGE", lambda: "• Solana is currently analysis + SHADOW only; LIVE Solana signing is intentionally disabled.")
    text = p.help_page()
    assert "separate guarded LIVE AUTO path" in text
    assert "LIVE Solana signing is intentionally disabled" not in text
