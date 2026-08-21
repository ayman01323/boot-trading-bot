from __future__ import annotations

from types import SimpleNamespace

from learnerbot import telegram_paspuss_ai_brand_patch as brand


FORBIDDEN_USER_WORDS = (
    "GPT",
    "Gemini",
    "Claude",
    "Copilot",
    "DeepSeek",
    "AI Council",
    "Leader",
    "AI opinions",
    "AI agents",
)


def _assert_native_paspuss(text: str) -> None:
    assert "PasPuss" in text
    for word in FORBIDDEN_USER_WORDS:
        assert word not in text


def test_non_master_status_is_native_paspuss_without_internal_mechanics(monkeypatch) -> None:
    session = {"mode": "user", "question": "what is cats best food"}
    for stage in ("asking", "leader", "resumed", "done", "failed"):
        text = brand._status_text(session, stage, valid=4)
        _assert_native_paspuss(text)
    assert "PasPuss is working on your question" in brand._status_text(session, "asking")


def test_non_master_menu_is_paspuss_and_hides_opinions(monkeypatch) -> None:
    monkeypatch.setattr(brand, "_is_master", lambda app, tid: False)
    monkeypatch.setattr(
        brand,
        "_PREV_MENU_KEYBOARD",
        lambda app, tid: {
            "inline_keyboard": [
                [{"text": "Ask SiBot", "callback_data": "aic:ask"}],
                [{"text": "View AI opinions", "callback_data": "aic:view:abc"}],
                [{"text": "Main Menu", "callback_data": "menu:home"}],
            ]
        },
    )
    kb = brand.menu_keyboard(SimpleNamespace(), 123)
    buttons = [button for row in kb["inline_keyboard"] for button in row]
    assert any(button["callback_data"] == "aic:ask" and "PasPuss AI" in button["text"] for button in buttons)
    assert not any(str(button.get("callback_data") or "").startswith("aic:view:") for button in buttons)
    assert "SiBot" not in str(kb)


def test_non_master_home_never_explains_internal_providers(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(brand, "_is_master", lambda app, tid: False)
    monkeypatch.setattr(brand._ui, "_send", lambda app, tid, text, kb=None: sent.append((text, kb)))
    brand._home(SimpleNamespace(), 123)
    assert len(sent) == 1
    text, kb = sent[0]
    _assert_native_paspuss(text)
    assert "Ask anything in one message" in text
    assert "PasPuss AI" in str(kb)
    assert "aic:view:" not in str(kb)


def test_fallback_answer_does_not_reveal_provider_or_leader(monkeypatch) -> None:
    session = {
        "mode": "user",
        "session_id": "260821141500-abcd",
        "answers": {"gemini": {"status": "DONE", "answer": "Feed a nutritionally complete cat food."}},
        "telegram": {},
    }
    sent = []
    monkeypatch.setattr(brand._friendly._council, "load_session", lambda app, sid: dict(session))
    monkeypatch.setattr(brand._friendly._council, "run_leader", lambda app, sid, leader: {"status": "FAILED", "answer": ""})
    monkeypatch.setattr(brand._friendly, "_best_available_answer", lambda s: ("gemini", "Feed a nutritionally complete cat food."))
    monkeypatch.setattr(brand._friendly, "_status_message", lambda app, tid, s, text, keyboard=None: s)
    monkeypatch.setattr(brand._friendly, "_chat_action", lambda app, tid: None)
    monkeypatch.setattr(brand._friendly, "_mark_delivered", lambda app, s, fallback=False: None)
    monkeypatch.setattr(
        brand._friendly,
        "_send_final_reply",
        lambda app, tid, s, title, body, keyboard=None: sent.append((title, body, keyboard)),
    )
    brand._finish_user_from_answers(SimpleNamespace(), 123, session["session_id"])
    assert sent
    title, body, keyboard = sent[0]
    _assert_native_paspuss(title)
    for word in FORBIDDEN_USER_WORDS:
        assert word not in body
    assert "gemini" not in body.lower()
    assert "aic:view:" not in str(keyboard)


def test_rate_limit_error_is_rebranded_for_non_master(monkeypatch) -> None:
    sent = []
    brand._cui._PENDING["123"] = "question"
    monkeypatch.setattr(brand, "_is_master", lambda app, tid: False)
    monkeypatch.setattr(
        brand._cui,
        "_start_question",
        lambda app, tid, text: (_ for _ in ()).throw(
            RuntimeError("Ask SiBot is rate-limited to protect AI credit. Try again in about 20 seconds.")
        ),
    )
    monkeypatch.setattr(brand._ui, "_send", lambda app, tid, text, kb=None: sent.append(text))
    monkeypatch.setattr(brand, "menu_keyboard", lambda app, tid: {"inline_keyboard": []})
    assert brand._handle_pending(SimpleNamespace(), 123, "hello") is True
    assert sent
    _assert_native_paspuss(sent[0])
    assert "SiBot" not in sent[0]
    assert "AI credit" not in sent[0]
