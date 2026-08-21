from __future__ import annotations

from types import SimpleNamespace

from learnerbot import telegram_paspuss_clean_chat_patch as clean


def test_progress_text_never_repeats_user_question() -> None:
    question = "UNIQUE QUESTION THAT MUST NOT BE PUSHED AGAIN"
    session = {"mode": "master", "question": question}
    for stage in ("asking", "leader", "master_ready", "resumed"):
        text = clean._status_text(session, stage, valid=5)
        assert "PasPuss is working on your question" in text
        assert question not in text
        assert "Your question:" not in text


def test_initial_progress_message_does_not_reply_to_or_quote_original(monkeypatch) -> None:
    session = {
        "telegram": {"question_message_id": 777},
        "session_id": "abc",
    }
    calls = []

    monkeypatch.setattr(clean._friendly, "_edit_message", lambda *args, **kwargs: False)
    monkeypatch.setattr(clean._friendly, "_send_api_message", lambda *args, **kwargs: calls.append((args, kwargs)) or {"message_id": 888})
    monkeypatch.setattr(clean._friendly, "_save_telegram_meta", lambda app, s, **values: {**s, "telegram": {**s.get("telegram", {}), **values}})

    updated = clean._status_message(SimpleNamespace(), 123, session, "working")
    assert calls
    _args, kwargs = calls[0]
    assert kwargs.get("reply_to_message_id") is None
    assert updated["telegram"]["progress_message_id"] == 888


def test_dense_single_line_answer_gets_readable_paragraph_spacing() -> None:
    raw = (
        "Cats generally do best on a nutritionally complete food appropriate for their life stage. "
        "Look for a product labelled complete rather than complementary. "
        "Wet food can help with hydration and many cats enjoy it. "
        "Dry food can also be nutritionally complete and is convenient. "
        "The best choice depends on age, health, weight and preferences. "
        "A veterinarian can help if the cat has a medical condition."
    )
    formatted = clean._organise_answer_text(raw)
    assert "\n\n" in formatted
    assert formatted.startswith("Cats generally")
    assert "A veterinarian" in formatted


def test_markdown_is_simplified_for_telegram_plain_text() -> None:
    raw = "### Good choices\n- **Complete wet food**\n- **Complete dry food**"
    formatted = clean._organise_answer_text(raw)
    assert "###" not in formatted
    assert "**" not in formatted
    assert "• Complete wet food" in formatted
    assert "• Complete dry food" in formatted


def test_leader_prompt_requests_mobile_friendly_spacing() -> None:
    session = {"question": "What should I feed my cat?", "answers": {}}
    prompt = clean._leader_prompt(session, "gpt")
    assert "blank line between paragraphs" in prompt
    assert "Do not compress the answer into one dense block" in prompt
    assert "Do not repeat the user's question" in prompt


def test_progress_message_is_deleted_after_success(monkeypatch) -> None:
    session = {
        "session_id": "abc",
        "question": "What should I feed my cat?",
        "answers": {"gpt": {"status": "DONE", "answer": "draft"}},
        "telegram": {"progress_message_id": 42},
    }
    deleted = []
    sent = []

    monkeypatch.setattr(clean._friendly._council, "load_session", lambda app, sid: dict(session))
    monkeypatch.setattr(clean._friendly._council, "run_leader", lambda app, sid, leader: {"status": "DONE", "answer": "First sentence. Second sentence. Third sentence."})
    monkeypatch.setattr(clean._friendly, "_status_message", lambda app, tid, s, text, keyboard=None: s)
    monkeypatch.setattr(clean._friendly, "_chat_action", lambda app, tid: None)
    monkeypatch.setattr(clean._friendly, "_mark_delivered", lambda app, s, fallback=False: None)
    monkeypatch.setattr(clean, "_delete_progress_message", lambda app, tid, s: deleted.append(s["telegram"]["progress_message_id"]))
    monkeypatch.setattr(clean, "_send_final_reply", lambda app, tid, s, title, body, keyboard=None: sent.append((title, body)))

    clean._finish_user_from_answers(SimpleNamespace(), 123, "abc")
    assert deleted == [42]
    assert len(sent) == 1
    assert sent[0][0] == "🐾 PasPuss AI"


def test_live_question_does_not_fallback_to_offline_independent_answer(monkeypatch) -> None:
    session = {
        "session_id": "live1",
        "question": "What is the current temperature in London?",
        "answers": {"gemini": {"status": "DONE", "answer": "I do not have access to live weather feeds."}},
        "telegram": {},
    }
    sent = []
    fallback_calls = []

    monkeypatch.setattr(clean._friendly._council, "load_session", lambda app, sid: dict(session))
    monkeypatch.setattr(clean._friendly._council, "run_leader", lambda app, sid, leader: {"status": "FAILED", "answer": ""})
    monkeypatch.setattr(clean._friendly, "_best_available_answer", lambda s: fallback_calls.append(True) or ("gemini", "offline draft"))
    monkeypatch.setattr(clean, "_direct_live_retry", lambda s: "London is 19°C right now.")
    monkeypatch.setattr(clean._friendly, "_status_message", lambda app, tid, s, text, keyboard=None: s)
    monkeypatch.setattr(clean._friendly, "_chat_action", lambda app, tid: None)
    monkeypatch.setattr(clean._friendly, "_mark_delivered", lambda app, s, fallback=False: None)
    monkeypatch.setattr(clean, "_delete_progress_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(clean, "_send_final_reply", lambda app, tid, s, title, body, keyboard=None: sent.append(body))

    clean._finish_user_from_answers(SimpleNamespace(), 123, "live1")
    assert fallback_calls == []
    assert sent == ["London is 19°C right now."]


def test_live_offline_refusal_is_replaced_by_live_retry(monkeypatch) -> None:
    session = {
        "session_id": "live2",
        "question": "What is the temperature in London right now?",
        "answers": {"gpt": {"status": "DONE", "answer": "draft"}},
        "telegram": {},
    }
    sent = []

    monkeypatch.setattr(clean._friendly._council, "load_session", lambda app, sid: dict(session))
    monkeypatch.setattr(
        clean._friendly._council,
        "run_leader",
        lambda app, sid, leader: {
            "status": "DONE",
            "answer": "I cannot provide the current real-time temperature because I do not have access to live weather feeds.",
        },
    )
    monkeypatch.setattr(clean, "_direct_live_retry", lambda s: clean._live.LIVE_UNAVAILABLE_TEXT)
    monkeypatch.setattr(clean._friendly, "_status_message", lambda app, tid, s, text, keyboard=None: s)
    monkeypatch.setattr(clean._friendly, "_chat_action", lambda app, tid: None)
    monkeypatch.setattr(clean._friendly, "_mark_delivered", lambda app, s, fallback=False: None)
    monkeypatch.setattr(clean, "_delete_progress_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(clean, "_send_final_reply", lambda app, tid, s, title, body, keyboard=None: sent.append(body))

    clean._finish_user_from_answers(SimpleNamespace(), 123, "live2")
    assert sent == [clean._live.LIVE_UNAVAILABLE_TEXT]
    assert "do not have access" not in sent[0].lower()
