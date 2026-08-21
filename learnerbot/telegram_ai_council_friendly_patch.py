from __future__ import annotations

import html
import json
import threading
import time
from pathlib import Path

from . import ai_council as _council
from . import telegram as _tg
from . import telegram_ai_council_patch as _cui
from . import telegram_ui as _ui

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_START_MENU_THREAD = _ui.start_menu_thread
_QUESTION_MESSAGE_IDS: dict[str, int] = {}
_RESUME_MAX_AGE_SECONDS = 2 * 60 * 60


def _question_excerpt(question: str, limit: int = 180) -> str:
    q = " ".join(str(question or "").split())
    if len(q) <= limit:
        return q
    return q[: limit - 1].rstrip() + "…"


def _send_api_message(app, tid, text: str, keyboard=None, *, reply_to_message_id=None):
    payload = {
        "chat_id": str(tid),
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    if reply_to_message_id:
        payload["reply_parameters"] = {
            "message_id": int(reply_to_message_id),
            "allow_sending_without_reply": True,
        }
    return _tg._json("sendMessage", app.telegram_bot_token, payload=payload, timeout=20) or {}


def _edit_message(app, tid, message_id, text: str, keyboard=None) -> bool:
    if not message_id:
        return False
    payload = {
        "chat_id": str(tid),
        "message_id": int(message_id),
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    try:
        _tg._json("editMessageText", app.telegram_bot_token, payload=payload, timeout=20)
        return True
    except Exception:
        return False


def _chat_action(app, tid, action: str = "typing") -> None:
    try:
        _tg._json("sendChatAction", app.telegram_bot_token, payload={"chat_id": str(tid), "action": action}, timeout=10)
    except Exception:
        pass


def _telegram_meta(session: dict) -> dict:
    value = session.get("telegram")
    return dict(value) if isinstance(value, dict) else {}


def _save_telegram_meta(app, session: dict, **values) -> dict:
    meta = _telegram_meta(session)
    meta.update({k: v for k, v in values.items() if v is not None})
    session["telegram"] = meta
    session["updated_epoch"] = int(time.time())
    return _council.save_session(app, session)


def _status_message(app, tid, session: dict, text: str, keyboard=None) -> dict:
    meta = _telegram_meta(session)
    message_id = meta.get("progress_message_id")
    if message_id and _edit_message(app, tid, message_id, text, keyboard):
        return session
    result = _send_api_message(
        app,
        tid,
        text,
        keyboard,
        reply_to_message_id=meta.get("question_message_id"),
    )
    return _save_telegram_meta(app, session, progress_message_id=result.get("message_id"))


def _status_text(session: dict, stage: str, *, valid: int | None = None) -> str:
    master = str(session.get("mode") or "") == "master"
    q = html.escape(_question_excerpt(str(session.get("question") or "")))
    title = "🧠 <b>AI Council</b>" if master else "🤖 <b>SiBot is working on your question</b>"
    if stage == "asking":
        detail = "Asking GPT, Gemini, Claude, Copilot and DeepSeek independently…"
    elif stage == "leader":
        detail = f"{valid or 0}/5 AI opinions received. GPT is combining them into one clear answer…"
    elif stage == "master_ready":
        detail = f"{valid or 0}/5 AI opinions received. Choose the Leader below."
    elif stage == "done":
        detail = f"✅ Answer ready below. {valid or 0}/5 AI agents contributed."
    elif stage == "failed":
        detail = "⚠️ SiBot could not get a usable AI reply. You can try the question again."
    elif stage == "resumed":
        detail = "♻️ The bot restarted while this question was running. SiBot has resumed it automatically."
    else:
        detail = stage
    return f"{title}\n\n<b>Your question:</b> {q}\n\n{detail}"


def _user_keyboard(session_id: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "👀 View AI opinions", "callback_data": f"aic:view:{session_id}"}],
        [{"text": "✍️ Ask another question", "callback_data": "aic:ask"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def _failure_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "🔄 Try again", "callback_data": "aic:ask"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def _split_raw(text: str, limit: int = 3000) -> list[str]:
    text = str(text or "").strip() or "(no answer returned)"
    out: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def _send_final_reply(app, tid, session: dict, title: str, body: str, keyboard=None) -> None:
    chunks = _split_raw(body)
    meta = _telegram_meta(session)
    reply_to = meta.get("question_message_id")
    for idx, chunk in enumerate(chunks):
        suffix = f" <i>({idx + 1}/{len(chunks)})</i>" if len(chunks) > 1 else ""
        text = f"<b>{html.escape(title)}</b>{suffix}\n\n{html.escape(chunk)}"
        _send_api_message(
            app,
            tid,
            text,
            keyboard if idx == len(chunks) - 1 else None,
            reply_to_message_id=reply_to if idx == 0 else None,
        )


def _best_available_answer(session: dict) -> tuple[str, str] | tuple[None, None]:
    answers = session.get("answers") or {}
    for provider in ("gpt", "gemini", "claude", "copilot", "deepseek"):
        row = answers.get(provider) or {}
        answer = str(row.get("answer") or "").strip()
        if str(row.get("status") or "") == "DONE" and answer:
            return provider, answer
    return None, None


def _mark_delivered(app, session: dict, *, fallback: bool = False) -> None:
    _save_telegram_meta(
        app,
        session,
        delivered_epoch=int(time.time()),
        final_delivery="fallback" if fallback else "leader",
    )


def _finish_user_from_answers(app, tid, session_id: str) -> None:
    session = _council.load_session(app, session_id)
    valid = sum(1 for row in (session.get("answers") or {}).values() if str((row or {}).get("status") or "") == "DONE")
    if valid == 0:
        session = _status_message(app, tid, session, _status_text(session, "failed"), _failure_keyboard())
        _send_final_reply(
            app,
            tid,
            session,
            "🤖 SiBot",
            "I couldn't reach any of the AI agents for this question. Please try again. If it happens again, the MASTER can open the AI opinions/details to see which provider is unavailable.",
            _failure_keyboard(),
        )
        return

    session = _status_message(app, tid, session, _status_text(session, "leader", valid=valid))
    _chat_action(app, tid)
    try:
        result = _council.run_leader(app, session_id, "gpt")
    except Exception as exc:
        result = {"status": "FAILED", "error": str(exc), "answer": ""}

    session = _council.load_session(app, session_id)
    if str(result.get("status") or "") == "DONE" and str(result.get("answer") or "").strip():
        _send_final_reply(app, tid, session, "🤖 SiBot — final answer", str(result.get("answer") or ""), _user_keyboard(session_id))
        _mark_delivered(app, session, fallback=False)
        session = _council.load_session(app, session_id)
        _status_message(app, tid, session, _status_text(session, "done", valid=valid), _user_keyboard(session_id))
        return

    provider, fallback = _best_available_answer(session)
    if fallback:
        body = (
            "GPT could not complete the final Council synthesis, so I am giving you the best available AI answer instead. "
            f"This one came from {str(provider).upper()}.\n\n{fallback}"
        )
        _send_final_reply(app, tid, session, "🤖 SiBot — answer", body, _user_keyboard(session_id))
        _mark_delivered(app, session, fallback=True)
        session = _council.load_session(app, session_id)
        _status_message(app, tid, session, _status_text(session, "done", valid=valid), _user_keyboard(session_id))
        return

    _status_message(app, tid, session, _status_text(session, "failed"), _failure_keyboard())
    _send_final_reply(app, tid, session, "🤖 SiBot", "I couldn't complete this answer. Please tap Try again.", _failure_keyboard())


def _process_question(app, tid, session_id: str, master_mode: bool) -> None:
    key = (str(tid), session_id)
    try:
        session = _council.load_session(app, session_id)
        session = _status_message(app, tid, session, _status_text(session, "asking"))
        _chat_action(app, tid)
        session = _council.run_independent_answers(app, session_id)
        valid = sum(1 for row in (session.get("answers") or {}).values() if str((row or {}).get("status") or "") == "DONE")

        if master_mode:
            if valid == 0:
                _status_message(app, tid, session, _status_text(session, "failed"), _failure_keyboard())
                return
            _status_message(app, tid, session, _status_text(session, "master_ready", valid=valid), _cui._leader_keyboard(session_id))
            return

        _finish_user_from_answers(app, tid, session_id)
    except Exception as exc:
        try:
            session = _council.load_session(app, session_id)
            _status_message(app, tid, session, _status_text(session, "failed"), _failure_keyboard())
            _send_final_reply(
                app,
                tid,
                session,
                "🤖 SiBot",
                "Something interrupted this AI request. Your question is saved, so you can retry it safely.",
                _failure_keyboard(),
            )
        except Exception:
            _ui._send(app, tid, f"⚠️ SiBot request interrupted: <code>{html.escape(str(exc)[:300])}</code>")
    finally:
        with _cui._LOCK:
            _cui._INFLIGHT.discard(key)


def _start_question(app, tid, question: str) -> None:
    master_mode = _cui._master(app, tid)
    mode = "master" if master_mode else "user"
    session = _council.create_session(app, tid, question, mode=mode)
    session_id = str(session["session_id"])
    question_message_id = _QUESTION_MESSAGE_IDS.pop(str(tid), None)
    session = _save_telegram_meta(app, session, question_message_id=question_message_id)
    session = _status_message(app, tid, session, _status_text(session, "asking"))

    key = (str(tid), session_id)
    with _cui._LOCK:
        _cui._INFLIGHT.add(key)
    threading.Thread(
        target=_process_question,
        args=(app, tid, session_id, master_mode),
        name=f"ai-council-{session_id}",
        daemon=True,
    ).start()


def _leader_worker(app, tid, session_id: str, leader: str) -> None:
    key = (str(tid), f"{session_id}:{leader}")
    try:
        _chat_action(app, tid)
        result = _council.run_leader(app, session_id, leader)
        session = _council.load_session(app, session_id)
        valid = sum(1 for row in (session.get("answers") or {}).values() if str((row or {}).get("status") or "") == "DONE")
        if str(result.get("status") or "") == "DONE" and str(result.get("answer") or "").strip():
            _send_final_reply(
                app,
                tid,
                session,
                f"👑 {leader.upper()} Leader — consolidated answer",
                str(result.get("answer") or ""),
                _cui._leader_keyboard(session_id),
            )
            _status_message(
                app,
                tid,
                session,
                f"🧠 <b>AI Council</b>\n\n✅ {html.escape(leader.upper())} Leader answered below. {valid}/5 agents contributed.\n\nChoose another Leader if you want a second view of the same original answers.",
                _cui._leader_keyboard(session_id),
            )
        else:
            _status_message(
                app,
                tid,
                session,
                f"🧠 <b>AI Council</b>\n\n⚠️ {html.escape(leader.upper())} could not complete this review. Choose another Leader.",
                _cui._leader_keyboard(session_id),
            )
    finally:
        with _cui._LOCK:
            _cui._INFLIGHT.discard(key)


def _start_leader(app, tid, session_id: str, leader: str) -> None:
    key = (str(tid), f"{session_id}:{leader}")
    with _cui._LOCK:
        if key in _cui._INFLIGHT:
            return
        _cui._INFLIGHT.add(key)
    try:
        session = _council.load_session(app, session_id)
        _status_message(
            app,
            tid,
            session,
            f"🧠 <b>AI Council</b>\n\n👑 {html.escape(leader.upper())} is reviewing the locked AI opinions now…",
        )
    except Exception:
        pass
    threading.Thread(
        target=_leader_worker,
        args=(app, tid, session_id, leader),
        name=f"ai-leader-{leader}-{session_id}",
        daemon=True,
    ).start()


def _home(app, tid) -> None:
    if _cui._master(app, tid):
        text = (
            "<b>🧠 AI Council</b>\n\n"
            "Ask one question. The five AI agents think independently, but their raw replies stay tucked away unless you ask to see them.\n\n"
            "When the opinions are ready, choose GPT, Gemini, Claude, Copilot or DeepSeek as Leader. You can choose another Leader afterwards to get a second judgement from the same original evidence."
        )
        keyboard = {"inline_keyboard": [
            [{"text": "✍️ Ask the AI Council", "callback_data": "aic:ask"}],
            [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
        ]}
    else:
        text = (
            "<b>💬 Ask SiBot</b>\n\n"
            "Ask anything in one message. SiBot quietly consults GPT, Gemini, Claude, Copilot and DeepSeek, then GPT turns the useful parts into one clear reply.\n\n"
            "You receive one final answer—not five messages. If you want the individual AI opinions, tap <b>View AI opinions</b> afterwards."
        )
        keyboard = {"inline_keyboard": [
            [{"text": "✍️ Ask SiBot", "callback_data": "aic:ask"}],
            [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
        ]}
    _ui._send(app, tid, text, keyboard)


def _resume_session(app, session: dict) -> None:
    tid = str(session.get("chat_id") or "")
    session_id = str(session.get("session_id") or "")
    status = str(session.get("status") or "")
    mode = str(session.get("mode") or "user")
    if not tid or not session_id:
        return
    try:
        if not _ui._auth(app, tid):
            return
    except Exception:
        return

    key = (tid, session_id)
    if status in {"QUEUED", "ASKING_AGENTS"}:
        with _cui._LOCK:
            if key in _cui._INFLIGHT:
                return
            _cui._INFLIGHT.add(key)
        session = _status_message(app, tid, session, _status_text(session, "resumed"))
        threading.Thread(
            target=_process_question,
            args=(app, tid, session_id, mode == "master"),
            name=f"ai-council-resume-{session_id}",
            daemon=True,
        ).start()
        return

    if status == "ANSWERS_READY":
        if mode == "master":
            valid = sum(1 for row in (session.get("answers") or {}).values() if str((row or {}).get("status") or "") == "DONE")
            _status_message(app, tid, session, _status_text(session, "master_ready", valid=valid), _cui._leader_keyboard(session_id))
            return
        with _cui._LOCK:
            if key in _cui._INFLIGHT:
                return
            _cui._INFLIGHT.add(key)
        threading.Thread(
            target=_resume_user_finish,
            args=(app, tid, session_id, key),
            name=f"ai-council-finish-{session_id}",
            daemon=True,
        ).start()


def _resume_user_finish(app, tid, session_id: str, key) -> None:
    try:
        _finish_user_from_answers(app, tid, session_id)
    finally:
        with _cui._LOCK:
            _cui._INFLIGHT.discard(key)


def _resume_stale_sessions(app) -> None:
    root = Path(app.data_dir) / "ai_council"
    if not root.exists():
        return
    now = int(time.time())
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]
    for path in files:
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
            updated = int(session.get("updated_epoch") or session.get("created_epoch") or 0)
            if not updated or now - updated > _RESUME_MAX_AGE_SECONDS:
                continue
            if _telegram_meta(session).get("delivered_epoch"):
                continue
            _resume_session(app, session)
        except Exception as exc:
            print("[ai-council-resume]", path.name, type(exc).__name__, exc)


def start_menu_thread(app):
    threading.Thread(target=_resume_stale_sessions, args=(app,), name="ai-council-recovery", daemon=True).start()
    return _PREV_START_MENU_THREAD(app)


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    if tid is not None and _cui._PENDING.get(str(tid)) == "question":
        message_id = message.get("message_id")
        if message_id:
            _QUESTION_MESSAGE_IDS[str(tid)] = int(message_id)
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_ai_council_friendly_patch_installed", False):
        return
    _cui._process_question = _process_question
    _cui._start_question = _start_question
    _cui._leader_worker = _leader_worker
    _cui._start_leader = _start_leader
    _cui._home = _home
    _ui.handle_update = handle_update
    _ui.start_menu_thread = start_menu_thread
    _ui._ai_council_friendly_patch_installed = True


install()
