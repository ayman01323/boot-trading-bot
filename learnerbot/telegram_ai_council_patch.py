from __future__ import annotations

import copy
import html
import threading

from . import ai_council as _council
from . import telegram_ui as _ui
from .user_registry import is_master

_PREV_MENU = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update
_PENDING: dict[str, str] = {}
_INFLIGHT: set[tuple[str, str]] = set()
_LOCK = threading.Lock()


def _master(app, tid) -> bool:
    try:
        return bool(is_master(app.csv_dir, tid))
    except Exception:
        return False


def _insert_button(rows: list[list[dict]], *, text: str, data: str) -> list[list[dict]]:
    if any(any(str(button.get("callback_data") or "") == data for button in row) for row in rows):
        return rows
    insert_at = len(rows)
    for idx, row in enumerate(rows):
        if any(str(button.get("callback_data") or "") in {"menu:status", "menu:help"} for button in row):
            insert_at = idx
            break
    rows.insert(insert_at, [{"text": text, "callback_data": data}])
    return rows


def menu_keyboard(app=None, chat_id=None):
    kb = copy.deepcopy(_PREV_MENU(app, chat_id))
    rows = list(kb.get("inline_keyboard") or [])
    if app is None or chat_id is None:
        return {"inline_keyboard": rows}
    if _master(app, chat_id):
        rows = _insert_button(rows, text="🧠 AI Council", data="aic:home")
    else:
        rows = _insert_button(rows, text="💬 Ask SiBot", data="aic:ask")
    return {"inline_keyboard": rows}


def _answer_callback(app, cb, text: str = "") -> None:
    cqid = (cb or {}).get("id")
    if not cqid:
        return
    try:
        _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
    except Exception:
        pass


def _send_chunks(app, tid, title: str, body: str, keyboard=None) -> None:
    safe = html.escape(str(body or "").strip())
    if not safe:
        safe = "(no answer returned)"
    limit = 3300
    chunks = [safe[i : i + limit] for i in range(0, len(safe), limit)] or [safe]
    for idx, chunk in enumerate(chunks):
        suffix = f" <i>({idx + 1}/{len(chunks)})</i>" if len(chunks) > 1 else ""
        text = f"<b>{html.escape(title)}</b>{suffix}\n\n{chunk}"
        _ui._send(app, tid, text, keyboard if idx == len(chunks) - 1 else None)


def _leader_keyboard(session_id: str, *, include_view: bool = True):
    rows = [
        [
            {"text": "GPT", "callback_data": f"aic:lead:gpt:{session_id}"},
            {"text": "Gemini", "callback_data": f"aic:lead:gemini:{session_id}"},
        ],
        [
            {"text": "Claude", "callback_data": f"aic:lead:claude:{session_id}"},
            {"text": "Copilot", "callback_data": f"aic:lead:copilot:{session_id}"},
        ],
        [{"text": "DeepSeek", "callback_data": f"aic:lead:deepseek:{session_id}"}],
    ]
    if include_view:
        rows.append([{"text": "📖 Original AI answers", "callback_data": f"aic:view:{session_id}"}])
    rows.append([{"text": "✍️ Ask another question", "callback_data": "aic:ask"}])
    rows.append([{"text": "⬅️ Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def _user_done_keyboard():
    return {"inline_keyboard": [
        [{"text": "✍️ Ask SiBot another question", "callback_data": "aic:ask"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def _home(app, tid):
    if _master(app, tid):
        text = "\n".join([
            "<b>🧠 AI COUNCIL</b>",
            "━━━━━━━━━━━━",
            "",
            "Ask one question. GPT, Gemini, Claude, Copilot and DeepSeek are asked independently in parallel.",
            "",
            "After the original answers are stored, choose any AI as Leader. The Leader sees the same original answers and produces one consolidated reply.",
            "You can then choose a different Leader for a second independent synthesis of the <b>same original answers</b>.",
            "",
            "<i>AI Council is advisory only. It cannot trade, deploy, sign, transfer assets or bypass LIVE/capital/safety controls.</i>",
        ])
        kb = {"inline_keyboard": [
            [{"text": "✍️ Ask all AIs", "callback_data": "aic:ask"}],
            [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
        ]}
    else:
        text = "\n".join([
            "<b>💬 ASK SiBot</b>",
            "━━━━━━━━━━━━",
            "",
            "Your question is sent independently to all available AI agents.",
            "After their answers arrive, <b>GPT is the fixed SiBot Leader</b> and produces the final combined answer.",
        ])
        kb = {"inline_keyboard": [
            [{"text": "✍️ Ask SiBot", "callback_data": "aic:ask"}],
            [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
        ]}
    _ui._send(app, tid, text, kb)


def _prompt_question(app, tid) -> None:
    _PENDING[str(tid)] = "question"
    label = "AI Council" if _master(app, tid) else "SiBot"
    _ui._send(
        app,
        tid,
        f"<b>✍️ Ask {html.escape(label)}</b>\n\nSend your question in one message.\nSend <code>/cancel</code> to cancel.",
        {"inline_keyboard": [[{"text": "Cancel", "callback_data": "aic:cancel"}]]},
    )


def _show_original_answers(app, tid, session: dict) -> None:
    for provider in _council.PROVIDERS:
        row = (session.get("answers") or {}).get(provider) or {}
        status = str(row.get("status") or "FAILED")
        if status == "DONE":
            _send_chunks(app, tid, f"{provider.upper()} — original answer", str(row.get("answer") or ""))
        else:
            reason = html.escape(str(row.get("error") or "not available")[:500])
            _ui._send(app, tid, f"<b>{provider.upper()}</b> ⚠️ unavailable\n<code>{reason}</code>")


def _process_question(app, tid, session_id: str, master_mode: bool) -> None:
    key = (str(tid), session_id)
    try:
        session = _council.run_independent_answers(app, session_id)
        _show_original_answers(app, tid, session)
        valid = sum(1 for row in (session.get("answers") or {}).values() if str((row or {}).get("status") or "") == "DONE")
        if valid == 0:
            _ui._send(app, tid, "⚠️ <b>No AI agent returned a usable answer.</b>\nCheck the provider credentials/CLI health and try again.", _user_done_keyboard())
            return

        if master_mode:
            _ui._send(
                app,
                tid,
                f"<b>👑 Select AI Leader</b>\n\nOriginal answers are now locked for session <code>{html.escape(session_id)}</code>.\nChoose a Leader to examine those same answers and issue one consolidated reply.",
                _leader_keyboard(session_id),
            )
            return

        _ui._send(app, tid, "<b>👑 GPT Leader</b>\n\nGPT is now examining the original AI answers and preparing SiBot's final reply…")
        try:
            result = _council.run_leader(app, session_id, "gpt")
        except Exception as exc:
            _ui._send(app, tid, f"⚠️ <b>GPT Leader could not complete.</b>\n<code>{html.escape(str(exc)[:600])}</code>", _user_done_keyboard())
            return
        if result.get("status") == "DONE":
            _send_chunks(app, tid, "🤖 SiBot — GPT Leader final answer", str(result.get("answer") or ""), _user_done_keyboard())
        else:
            _ui._send(app, tid, f"⚠️ <b>GPT Leader failed.</b>\n<code>{html.escape(str(result.get('error') or '')[:600])}</code>", _user_done_keyboard())
    except Exception as exc:
        _ui._send(app, tid, f"❌ <b>AI Council error</b>\n<code>{html.escape(str(exc)[:700])}</code>", _user_done_keyboard())
    finally:
        with _LOCK:
            _INFLIGHT.discard(key)


def _start_question(app, tid, question: str) -> None:
    master_mode = _master(app, tid)
    mode = "master" if master_mode else "user"
    session = _council.create_session(app, tid, question, mode=mode)
    session_id = str(session["session_id"])
    key = (str(tid), session_id)
    with _LOCK:
        _INFLIGHT.add(key)
    names = ", ".join(p.upper() for p in _council.PROVIDERS)
    _ui._send(
        app,
        tid,
        f"<b>{'🧠 AI Council' if master_mode else '💬 SiBot'}</b>\n\nSession <code>{html.escape(session_id)}</code>\nAsking independently in parallel: <b>{html.escape(names)}</b>.\n\nYou can keep using the bot while the answers are prepared.",
    )
    thread = threading.Thread(
        target=_process_question,
        args=(app, tid, session_id, master_mode),
        name=f"ai-council-{session_id}",
        daemon=True,
    )
    thread.start()


def _leader_worker(app, tid, session_id: str, leader: str) -> None:
    key = (str(tid), f"{session_id}:{leader}")
    try:
        result = _council.run_leader(app, session_id, leader)
        if result.get("status") == "DONE":
            _send_chunks(
                app,
                tid,
                f"👑 {leader.upper()} Leader — consolidated answer",
                str(result.get("answer") or ""),
                _leader_keyboard(session_id),
            )
        else:
            _ui._send(
                app,
                tid,
                f"⚠️ <b>{html.escape(leader.upper())} Leader failed.</b>\n<code>{html.escape(str(result.get('error') or '')[:600])}</code>",
                _leader_keyboard(session_id),
            )
    except Exception as exc:
        _ui._send(app, tid, f"❌ <b>Leader review error</b>\n<code>{html.escape(str(exc)[:700])}</code>", _leader_keyboard(session_id))
    finally:
        with _LOCK:
            _INFLIGHT.discard(key)


def _start_leader(app, tid, session_id: str, leader: str) -> None:
    key = (str(tid), f"{session_id}:{leader}")
    with _LOCK:
        if key in _INFLIGHT:
            _ui._send(app, tid, f"⏳ {html.escape(leader.upper())} is already reviewing this session.")
            return
        _INFLIGHT.add(key)
    _ui._send(app, tid, f"<b>👑 {html.escape(leader.upper())} Leader</b>\n\nReviewing the locked original answers from <code>{html.escape(session_id)}</code>…")
    threading.Thread(
        target=_leader_worker,
        args=(app, tid, session_id, leader),
        name=f"ai-leader-{leader}-{session_id}",
        daemon=True,
    ).start()


def _handle_pending(app, tid, text: str) -> bool:
    if _PENDING.get(str(tid)) != "question":
        return False
    if text.startswith("/"):
        _PENDING.pop(str(tid), None)
        if text.split(maxsplit=1)[0].split("@", 1)[0].lower() == "/cancel":
            _ui._send(app, tid, "✅ AI question cancelled.", menu_keyboard(app, tid))
            return True
        return False
    _PENDING.pop(str(tid), None)
    try:
        _start_question(app, tid, text)
    except Exception as exc:
        _ui._send(app, tid, f"❌ <b>Could not start AI question</b>\n<code>{html.escape(str(exc)[:700])}</code>", menu_keyboard(app, tid))
    return True


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data == "aic:home" or data == "aic:ask" or data == "aic:cancel" or data.startswith("aic:view:") or data.startswith("aic:lead:"):
            if tid is None or not _ui._auth(app, tid):
                _answer_callback(app, cb, "Not authorised")
                return
            _answer_callback(app, cb)
            if data == "aic:home":
                _PENDING.pop(str(tid), None)
                _home(app, tid)
                return
            if data == "aic:ask":
                _prompt_question(app, tid)
                return
            if data == "aic:cancel":
                _PENDING.pop(str(tid), None)
                _ui._send(app, tid, "✅ AI question cancelled.", menu_keyboard(app, tid))
                return
            if data.startswith("aic:view:"):
                session_id = data.split(":", 2)[2]
                try:
                    session = _council.load_session(app, session_id)
                    if str(session.get("chat_id") or "") != str(tid) and not _master(app, tid):
                        raise PermissionError("session belongs to another Telegram user")
                    _show_original_answers(app, tid, session)
                except Exception as exc:
                    _ui._send(app, tid, f"❌ <code>{html.escape(str(exc)[:600])}</code>")
                return
            if data.startswith("aic:lead:"):
                if not _master(app, tid):
                    _ui._send(app, tid, "⛔ Only the MASTER account can select or change the AI Leader.")
                    return
                parts = data.split(":", 3)
                if len(parts) != 4:
                    _ui._send(app, tid, "❌ Invalid Leader request.")
                    return
                leader, session_id = parts[2], parts[3]
                try:
                    session = _council.load_session(app, session_id)
                    if str(session.get("chat_id") or "") != str(tid):
                        raise PermissionError("AI Council session belongs to another Telegram user")
                    _start_leader(app, tid, session_id, leader)
                except Exception as exc:
                    _ui._send(app, tid, f"❌ <code>{html.escape(str(exc)[:600])}</code>")
                return

    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and _ui._auth(app, tid) and _handle_pending(app, tid, text):
        return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_ai_council_patch_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._ai_council_patch_installed = True


install()
