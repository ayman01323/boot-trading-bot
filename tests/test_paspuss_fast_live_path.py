from __future__ import annotations

import time
from types import SimpleNamespace

from learnerbot import telegram_paspuss_clean_chat_patch as clean


def test_live_question_skips_private_reviewer_wait(monkeypatch) -> None:
    session = {"session_id": "abc", "question": "What is the current temperature in London?"}
    delivered = []

    monkeypatch.setattr(clean._friendly._council, "load_session", lambda app, sid: dict(session))
    monkeypatch.setattr(clean._friendly, "_status_message", lambda app, tid, s, text, keyboard=None: s)
    monkeypatch.setattr(clean._friendly, "_chat_action", lambda app, tid: None)
    monkeypatch.setattr(
        clean._friendly._council,
        "run_independent_answers",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live question must not wait for reviewers")),
    )
    monkeypatch.setattr(clean, "_deliver_direct_live", lambda app, tid, s, sid: delivered.append((tid, sid)))

    clean._process_question(SimpleNamespace(), 123, "abc", False)
    assert delivered == [(123, "abc")]


def test_final_provider_deadline_returns_control(monkeypatch) -> None:
    started = time.monotonic()
    result = clean._within_deadline(lambda: time.sleep(0.15) or "late", seconds=0.02)
    elapsed = time.monotonic() - started
    assert result is None
    assert elapsed < 0.10
