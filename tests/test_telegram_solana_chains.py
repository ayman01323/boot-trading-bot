from learnerbot import telegram_solana_chains_patch as patch


def test_chains_page_shows_solana_active_shadow(monkeypatch):
    monkeypatch.setattr(patch, "_PREV_CHAINS_PAGE", lambda app: "<b>🌐 ENABLED CHAINS</b>\n\n• <b>Base</b> — chain 8453\n\nEnable/disable chains in <code>CSVbot/chains.csv</code>.")
    monkeypatch.setattr(patch._sol, "settings", lambda app: {"enabled": "true", "rpc_url": "https://rpc.example"})
    text = patch.chains_page(object())
    assert "<b>Solana</b>" in text
    assert "✅ ACTIVE" in text
    assert "SHADOW" in text
    assert "RPC configured" in text


def test_chains_page_shows_solana_inactive(monkeypatch):
    monkeypatch.setattr(patch, "_PREV_CHAINS_PAGE", lambda app: "<b>🌐 ENABLED CHAINS</b>")
    monkeypatch.setattr(patch._sol, "settings", lambda app: {"enabled": "false", "rpc_url": ""})
    text = patch.chains_page(object())
    assert "<b>Solana</b>" in text
    assert "🔴 INACTIVE" in text
    assert "RPC MISSING" in text
