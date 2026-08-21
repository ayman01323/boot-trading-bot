from __future__ import annotations

import time
from types import SimpleNamespace

from learnerbot import ai_council
from learnerbot import telegram_ai_council_friendly_patch as friendly


def _app(tmp_path):
    return SimpleNamespace(
        data_dir=tmp_path,
        csv_dir=tmp_path,
        telegram_bot_token="test-token",
    )


def _completed_session(app, *, mode="user"):
    session = ai_council.create_session(app, "123", "What should I do?", mode=mode)
    session["answers"] = {
        "gpt": {"status": "DONE", "answer": "GPT original"},
        "gemini": {"status": "DONE", "answer": "Gemini original"},
        "claude": {"status": "FAILED", "answer": "", "error": "offline"},
        "copilot": {"status": "DONE", "answer": "Copilot original"},
        "deepseek": {"status": "FAILED", "answer": "", "error": "offline"},
    }
    session["status"] = "ANSWERS_READY"
    return ai_council.save_session(app, session)


def test_user_gets_one_final_synthesis_without_automatic_raw_answer_dump(tmp_path, monkeypatch):
    app = _app(tmp_path)
    session = _completed_session(app)
    sent = []
    status = []

    monkeypatch.setattr(friendly, "_chat_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(friendly, "_status_message", lambda app, tid, session, text, keyboard=None: status.append(text) or session)
    monkeypatch.setattr(friendly, "_send_final_reply", lambda app, tid, session, title, body, keyboard=None: sent.append((title, body)))
    monkeypatch.setattr(
        ai_council,
        "run_leader",
        lambda app, session_id, leader: {"status": "DONE", "answer": "One clear combined answer"},
    )

    friendly._finish_user_from_answers(app, "123", session["session_id"])

    assert sent == [("🐾 PasPuss AI", "One clear combined answer")]
    assert any("PasPuss is working on your question" in text for text in status)
    assert all("GPT is combining" not in text for text in status)


def test_gpt_leader_failure_falls_back_to_best_available_answer(tmp_path, monkeypatch):
    app = _app(tmp_path)
    session = _completed_session(app)
    sent = []

    monkeypatch.setattr(friendly, "_chat_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(friendly, "_status_message", lambda app, tid, session, text, keyboard=None: session)
    monkeypatch.setattr(friendly, "_send_final_reply", lambda app, tid, session, title, body, keyboard=None: sent.append((title, body)))
    monkeypatch.setattr(
        ai_council,
        "run_leader",
        lambda app, session_id, leader: {"status": "FAILED", "answer": "", "error": "timeout"},
    )

    friendly._finish_user_from_answers(app, "123", session["session_id"])

    assert len(sent) == 1
    assert sent[0][0] == "🐾 PasPuss AI"
    assert sent[0][1] == "GPT original"
    assert "could not complete" not in sent[0][1]
    assert "Leader" not in sent[0][1]


def test_restart_recovery_resumes_recent_asking_session(tmp_path, monkeypatch):
    app = _app(tmp_path)
    session = ai_council.create_session(app, "123", "Resume me", mode="user")
    session["status"] = "ASKING_AGENTS"
    session["updated_epoch"] = int(time.time())
    ai_council.save_session(app, session)

    resumed = []

    monkeypatch.setattr(friendly._ui, "_auth", lambda app, tid: True)
    monkeypatch.setattr(friendly, "_status_message", lambda app, tid, session, text, keyboard=None: session)

    class ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, **_):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            resumed.append((self.target.__name__, self.args))

    monkeypatch.setattr(friendly.threading, "Thread", ImmediateThread)

    friendly._resume_stale_sessions(app)

    assert resumed
    assert resumed[0][0] == "_process_question"
    assert resumed[0][1][2] == session["session_id"]


def test_final_reply_is_threaded_to_original_question(tmp_path, monkeypatch):
    app = _app(tmp_path)
    session = _completed_session(app)
    session["telegram"] = {"question_message_id": 777}
    ai_council.save_session(app, session)
    payloads = []

    monkeypatch.setattr(
        friendly._tg,
        "_json",
        lambda method, token, payload=None, timeout=20, **kwargs: payloads.append((method, payload)) or {"message_id": 900},
    )

    friendly._send_final_reply(app, "123", session, "PasPuss AI", "Answer", friendly._user_keyboard(session["session_id"]))

    send = next(payload for method, payload in payloads if method == "sendMessage")
    assert send["reply_parameters"]["message_id"] == 777
    assert "PasPuss AI" in str(send["reply_markup"])
    assert "View AI opinions" not in str(send["reply_markup"])
    assert "aic:view:" not in str(send["reply_markup"])
