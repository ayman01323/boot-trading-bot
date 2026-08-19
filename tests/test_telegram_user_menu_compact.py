from types import SimpleNamespace


def test_non_master_menu_is_short_and_user_only(monkeypatch, tmp_path):
    from learnerbot import telegram_user_menu_compact_patch as patch

    monkeypatch.setattr(patch, "is_master", lambda csv_dir, chat_id: False)
    app = SimpleNamespace(csv_dir=tmp_path)

    kb = patch.menu_keyboard(app, "461513364")
    rows = kb["inline_keyboard"]
    buttons = [button for row in rows for button in row]
    texts = [button["text"] for button in buttons]
    callbacks = {button["callback_data"] for button in buttons}

    assert len(rows) == 5
    assert len(buttons) == 9
    assert texts == [
        "🤖 SiBot", "💰 Capital",
        "🔐 Wallets", "💱 Trading",
        "⚡ Auto", "🛰 Opportunities",
        "⏰ Reports & Alerts",
        "📡 Status", "❓ Help",
    ]
    assert "menu:myalerts" in callbacks
    assert not any("All Chains" in text or "EVM + SOL" in text for text in texts)
    assert "menu:control" not in callbacks
    assert "menu:autodeploy" not in callbacks
    assert "menu:products" not in callbacks
    assert "menu:power" not in callbacks


def test_master_menu_is_not_replaced(monkeypatch, tmp_path):
    from learnerbot import telegram_user_menu_compact_patch as patch

    expected = {"inline_keyboard": [[{"text": "MASTER", "callback_data": "menu:control"}]]}
    monkeypatch.setattr(patch, "is_master", lambda csv_dir, chat_id: True)
    monkeypatch.setattr(patch, "_PREV_MENU", lambda app=None, chat_id=None: expected)
    app = SimpleNamespace(csv_dir=tmp_path)

    assert patch.menu_keyboard(app, "5923828381") == expected
