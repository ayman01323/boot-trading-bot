from learnerbot import telegram_pending_command_patch as patch


def test_menu_bypasses_and_clears_pending_sibot_numeric_prompt(monkeypatch):
    tid = 123456
    calls = []
    patch._sibot_ui._PENDING[str(tid)] = "min_win_rate_pct"
    monkeypatch.setattr(patch, "_original_handle_update", lambda app, update: calls.append(update))

    patch.handle_update(object(), {"message": {"chat": {"id": tid}, "text": "/menu"}})

    assert str(tid) not in patch._sibot_ui._PENDING
    assert len(calls) == 1
    assert calls[0]["message"]["text"] == "/menu"


def test_cancel_clears_pending_without_parsing_as_number(monkeypatch):
    tid = 654321
    sent = []
    patch._sibot_ui._PENDING[str(tid)] = "min_win_rate_pct"
    monkeypatch.setattr(patch._ui, "_send", lambda app, chat_id, text, kb=None: sent.append((chat_id, text)))
    monkeypatch.setattr(patch._sibot_ui, "settings_keyboard", lambda app, chat_id: {"inline_keyboard": []})
    monkeypatch.setattr(patch, "_original_handle_update", lambda app, update: (_ for _ in ()).throw(AssertionError("must not forward /cancel")))

    patch.handle_update(object(), {"message": {"chat": {"id": tid}, "text": "/cancel"}})

    assert str(tid) not in patch._sibot_ui._PENDING
    assert sent == [(tid, "✅ SiBot setting change cancelled.")]
