from __future__ import annotations

import html

from . import telegram_ai_council_friendly_patch as _friendly
from . import telegram_ai_council_patch as _cui
from . import telegram_ui as _ui

_PREV_MENU_KEYBOARD = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update
_MASTER_HOME = _friendly._home
_MASTER_PROCESS = _friendly._process_question
_MASTER_FINISH = _friendly._finish_user_from_answers
_MASTER_STATUS_TEXT = _friendly._status_text
_MASTER_PROMPT = _cui._prompt_question
_MASTER_PENDING = _cui._handle_pending

_BRAND = "PasPuss AI"


def _is_master(app, tid) -> bool:
    return bool(_cui._master(app, tid))


def _rename_user_buttons(keyboard: dict) -> dict:
    rows = keyboard.get("inline_keyboard") or []
    for row in rows:
        for button in row:
            data = str(button.get("callback_data") or "")
            if data == "aic:ask":
                button["text"] = "🐾 PasPuss AI"
            elif data.startswith("aic:view:"):
                button["_paspuss_hide"] = True
    keyboard["inline_keyboard"] = [
        [button for button in row if not button.pop("_paspuss_hide", False)]
        for row in rows
    ]
    keyboard["inline_keyboard"] = [row for row in keyboard["inline_keyboard"] if row]
    return keyboard


def menu_keyboard(app=None, chat_id=None):
    keyboard = _PREV_MENU_KEYBOARD(app, chat_id)
    if app is None or chat_id is None or _is_master(app, chat_id):
        return keyboard
    return _rename_user_buttons(keyboard)


def _prompt_question(app, tid) -> None:
    if _is_master(app, tid):
        return _MASTER_PROMPT(app, tid)
    _cui._PENDING[str(tid)] = "question"
    _ui._send(
        app,
        tid,
        "<b>🐾 Ask PasPuss AI</b>\n\nSend your question in one message.\nSend <code>/cancel</code> to cancel.",
        {"inline_keyboard": [[{"text": "Cancel", "callback_data": "aic:cancel"}]]},
    )


def _status_text(session: dict, stage: str, *, valid: int | None = None) -> str:
    if str(session.get("mode") or "") == "master":
        return _MASTER_STATUS_TEXT(session, stage, valid=valid)
    q = html.escape(_friendly._question_excerpt(str(session.get("question") or "")))
    if stage in {"asking", "leader", "resumed"}:
        detail = "🐾 <b>PasPuss is working on your question…</b>"
    elif stage == "done":
        detail = "✅ <b>Your PasPuss AI answer is ready below.</b>"
    elif stage == "failed":
        detail = "⚠️ <b>PasPuss AI couldn’t answer right now.</b> Please try again."
    else:
        detail = "🐾 <b>PasPuss is working on your question…</b>"
    return f"<b>🐾 PasPuss AI</b>\n\n<b>Your question:</b> {q}\n\n{detail}"


def _user_keyboard(_session_id: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "🐾 Ask PasPuss AI again", "callback_data": "aic:ask"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def _failure_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "🔄 Try PasPuss AI again", "callback_data": "aic:ask"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def _home(app, tid) -> None:
    if _is_master(app, tid):
        return _MASTER_HOME(app, tid)
    _ui._send(
        app,
        tid,
        "<b>🐾 PasPuss AI</b>\n\nAsk anything in one message. PasPuss AI will reply here.",
        {"inline_keyboard": [
            [{"text": "🐾 Ask PasPuss AI", "callback_data": "aic:ask"}],
            [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
        ]},
    )


def _finish_user_from_answers(app, tid, session_id: str) -> None:
    session = _friendly._council.load_session(app, session_id)
    valid = sum(
        1
        for row in (session.get("answers") or {}).values()
        if str((row or {}).get("status") or "") == "DONE"
    )
    if valid == 0:
        session = _friendly._status_message(
            app, tid, session, _status_text(session, "failed"), _failure_keyboard()
        )
        _friendly._send_final_reply(
            app,
            tid,
            session,
            "🐾 PasPuss AI",
            "I couldn’t answer that right now. Please try again.",
            _failure_keyboard(),
        )
        return

    session = _friendly._status_message(app, tid, session, _status_text(session, "leader", valid=valid))
    _friendly._chat_action(app, tid)
    try:
        result = _friendly._council.run_leader(app, session_id, "gpt")
    except Exception:
        result = {"status": "FAILED", "answer": ""}

    session = _friendly._council.load_session(app, session_id)
    answer = str(result.get("answer") or "").strip()
    fallback = False
    if str(result.get("status") or "") != "DONE" or not answer:
        _provider, answer = _friendly._best_available_answer(session)
        fallback = bool(answer)

    if answer:
        _friendly._send_final_reply(
            app,
            tid,
            session,
            "🐾 PasPuss AI",
            answer,
            _user_keyboard(session_id),
        )
        _friendly._mark_delivered(app, session, fallback=fallback)
        session = _friendly._council.load_session(app, session_id)
        _friendly._status_message(
            app,
            tid,
            session,
            _status_text(session, "done", valid=valid),
            _user_keyboard(session_id),
        )
        return

    _friendly._status_message(app, tid, session, _status_text(session, "failed"), _failure_keyboard())
    _friendly._send_final_reply(
        app,
        tid,
        session,
        "🐾 PasPuss AI",
        "I couldn’t complete that answer. Please try again.",
        _failure_keyboard(),
    )


def _process_question(app, tid, session_id: str, master_mode: bool) -> None:
    if master_mode:
        return _MASTER_PROCESS(app, tid, session_id, master_mode)
    key = (str(tid), session_id)
    try:
        session = _friendly._council.load_session(app, session_id)
        session = _friendly._status_message(app, tid, session, _status_text(session, "asking"))
        _friendly._chat_action(app, tid)
        _friendly._council.run_independent_answers(app, session_id)
        _finish_user_from_answers(app, tid, session_id)
    except Exception:
        try:
            session = _friendly._council.load_session(app, session_id)
            _friendly._status_message(
                app, tid, session, _status_text(session, "failed"), _failure_keyboard()
            )
            _friendly._send_final_reply(
                app,
                tid,
                session,
                "🐾 PasPuss AI",
                "Something interrupted the reply. Please try your question again.",
                _failure_keyboard(),
            )
        except Exception:
            _ui._send(app, tid, "⚠️ PasPuss AI couldn’t answer right now. Please try again.")
    finally:
        with _cui._LOCK:
            _cui._INFLIGHT.discard(key)


def _handle_pending(app, tid, text: str) -> bool:
    if _is_master(app, tid):
        return _MASTER_PENDING(app, tid, text)
    if _cui._PENDING.get(str(tid)) != "question":
        return False
    if text.startswith("/"):
        _cui._PENDING.pop(str(tid), None)
        if text.split(maxsplit=1)[0].split("@", 1)[0].lower() == "/cancel":
            _ui._send(app, tid, "✅ PasPuss AI question cancelled.", menu_keyboard(app, tid))
            return True
        return False
    _cui._PENDING.pop(str(tid), None)
    try:
        _cui._start_question(app, tid, text)
    except Exception as exc:
        message = str(exc)
        message = message.replace("Ask SiBot", "PasPuss AI").replace("AI credit", "usage")
        _ui._send(
            app,
            tid,
            f"⚠️ <b>PasPuss AI</b>\n\n{html.escape(message)}",
            menu_keyboard(app, tid),
        )
    return True


def handle_update(app, update):
    cb = update.get("callback_query") or {}
    tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
    data = str(cb.get("data") or "")
    if tid is not None and not _is_master(app, tid):
        if data.startswith("aic:view:"):
            _cui._answer_callback(app, cb, "Not available")
            _home(app, tid)
            return
        if data == "aic:cancel":
            _cui._answer_callback(app, cb)
            _cui._PENDING.pop(str(tid), None)
            _ui._send(app, tid, "✅ PasPuss AI question cancelled.", menu_keyboard(app, tid))
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_paspuss_ai_brand_patch_installed", False):
        return
    _friendly._status_text = _status_text
    _friendly._user_keyboard = _user_keyboard
    _friendly._failure_keyboard = _failure_keyboard
    _friendly._finish_user_from_answers = _finish_user_from_answers
    _friendly._process_question = _process_question
    _friendly._home = _home
    _cui._prompt_question = _prompt_question
    _cui._handle_pending = _handle_pending
    _cui._process_question = _process_question
    _cui._home = _home
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._paspuss_ai_brand_patch_installed = True


install()
