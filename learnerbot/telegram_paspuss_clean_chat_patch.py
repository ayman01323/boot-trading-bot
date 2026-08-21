from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from . import ai_council_latency_patch as _latency  # noqa: F401
from . import ai_council_live_web_patch as _live
from . import telegram_ai_council_friendly_patch as _friendly
from . import telegram_ai_council_patch as _cui
from . import telegram_paspuss_ai_brand_patch as _brand
from . import telegram_ui as _ui

_ORIGINAL_LEADER_PROMPT = _brand._leader_prompt
_FINAL_ANSWER_DEADLINE_SECONDS = 25.0


def _status_text(session: dict, stage: str, *, valid: int | None = None) -> str:
    if stage == "failed":
        return "<b>🐾 PasPuss AI</b>\n\n⚠️ PasPuss AI couldn’t answer right now. Please try again."
    return "<b>🐾 PasPuss is working on your question…</b>"


def _status_message(app, tid, session: dict, text: str, keyboard=None) -> dict:
    """Maintain one small progress message without quoting/repeating the user's question."""
    meta = _friendly._telegram_meta(session)
    message_id = meta.get("progress_message_id")
    if message_id and _friendly._edit_message(app, tid, message_id, text, keyboard):
        return session
    result = _friendly._send_api_message(app, tid, text, keyboard)
    return _friendly._save_telegram_meta(app, session, progress_message_id=result.get("message_id"))


def _delete_progress_message(app, tid, session: dict) -> None:
    message_id = _friendly._telegram_meta(session).get("progress_message_id")
    if not message_id:
        return
    try:
        _friendly._tg._json(
            "deleteMessage",
            app.telegram_bot_token,
            payload={"chat_id": str(tid), "message_id": int(message_id)},
            timeout=15,
        )
    except Exception:
        pass


def _organise_answer_text(text: str) -> str:
    """Turn dense model output into readable Telegram plain text without changing meaning."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return raw

    raw = raw.replace("**", "").replace("__", "")
    cleaned: list[str] = []
    for source in raw.split("\n"):
        line = re.sub(r"[ \t]+", " ", source).strip()
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^[*-]\s+", "• ", line)
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        cleaned.append(line)

    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    nonempty = [line for line in cleaned if line]
    if len(nonempty) == 1 and len(nonempty[0]) >= 320:
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", nonempty[0])
        if len(sentences) >= 3:
            paragraphs = [" ".join(sentences[i : i + 2]).strip() for i in range(0, len(sentences), 2)]
            return "\n\n".join(p for p in paragraphs if p)

    if "" not in cleaned and len(cleaned) >= 2:
        out: list[str] = []
        for idx, line in enumerate(cleaned):
            if idx:
                prev = cleaned[idx - 1]
                prev_list = bool(re.match(r"^(?:•|\d+[.)])\s", prev))
                this_list = bool(re.match(r"^(?:•|\d+[.)])\s", line))
                if not (prev_list and this_list):
                    out.append("")
            out.append(line)
        cleaned = out

    return "\n".join(cleaned).strip()


def _leader_prompt(session: dict, leader: str) -> str:
    base = _ORIGINAL_LEADER_PROMPT(session, leader)
    return base + """

PRESENTATION RULES FOR THE USER-FACING ANSWER:
- Make it easy to read on a phone.
- Use short paragraphs, normally 1-3 sentences each.
- Put a blank line between paragraphs.
- Use simple bullet points only when they genuinely improve clarity.
- Use short section labels only when useful.
- Do not use markdown tables.
- Do not compress the answer into one dense block of text.
- Do not repeat the user's question before answering it.
"""


def _send_final_reply(app, tid, session: dict, title: str, body: str, keyboard=None) -> None:
    return _friendly._send_final_reply(app, tid, session, title, _organise_answer_text(body), keyboard)


def _within_deadline(fn, *, seconds: float = _FINAL_ANSWER_DEADLINE_SECONDS):
    """Return a result promptly even if an upstream provider stalls."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paspuss-final")
    future = pool.submit(fn)
    try:
        return future.result(timeout=seconds)
    except TimeoutError:
        future.cancel()
        return None
    except Exception:
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _direct_live_retry(session: dict) -> str:
    """Run the grounded live-answer path without waiting on Council reviewers."""
    prompt = _leader_prompt(session, "gpt")
    env = _live._http._runtime_env()
    result = _within_deadline(lambda: _live._call_openai(prompt, env))
    if result:
        rc, out, _err = result
        if rc == 0 and str(out or "").strip() and not _live._looks_like_offline_refusal(str(out)):
            return str(out).strip()
    return _live.LIVE_UNAVAILABLE_TEXT


def _direct_static_answer(session: dict) -> str:
    prompt = _leader_prompt(session, "gpt")
    result = _within_deadline(lambda: _friendly._council.call_provider("gpt", prompt))
    if result:
        rc, out, _err = result
        if rc == 0 and str(out or "").strip():
            return str(out).strip()
    return ""


def _deliver_direct_live(app, tid, session: dict, session_id: str) -> None:
    """Deliver the user answer first; persistence must never suppress Telegram output."""
    answer = _direct_live_retry(session)
    try:
        session = _friendly._council.load_session(app, session_id)
    except Exception:
        session = dict(session)

    # User-visible delivery is the primary operation. Both progress cleanup and answer
    # sending happen before any session bookkeeping that could fail independently.
    _delete_progress_message(app, tid, session)
    _send_final_reply(
        app,
        tid,
        session,
        "🐾 PasPuss AI",
        answer,
        _brand._user_keyboard(session_id),
    )

    # Bookkeeping is best-effort only. A stale/corrupt session id, disk issue, or
    # metadata write failure must never turn a valid answer into silence.
    try:
        session["status"] = "LEADER_READY"
        session["updated_epoch"] = int(time.time())
        _friendly._council.save_session(app, session)
    except Exception:
        pass
    try:
        _friendly._mark_delivered(app, session, fallback=False)
    except Exception:
        pass


def _finish_user_from_answers(app, tid, session_id: str) -> None:
    session = _friendly._council.load_session(app, session_id)
    valid = sum(
        1
        for row in (session.get("answers") or {}).values()
        if str((row or {}).get("status") or "") == "DONE"
    )
    question = str(session.get("question") or "")
    requires_live = _live._question_requires_live(question)

    if requires_live:
        _deliver_direct_live(app, tid, session, session_id)
        return

    if valid == 0:
        answer = _direct_static_answer(session)
        _delete_progress_message(app, tid, session)
        if answer:
            _send_final_reply(app, tid, session, "🐾 PasPuss AI", answer, _brand._user_keyboard(session_id))
            try:
                _friendly._mark_delivered(app, session, fallback=True)
            except Exception:
                pass
            return
        _send_final_reply(
            app,
            tid,
            session,
            "🐾 PasPuss AI",
            "I couldn’t answer that right now. Please try again.",
            _brand._failure_keyboard(),
        )
        return

    session = _friendly._status_message(app, tid, session, _status_text(session, "leader", valid=valid))
    _friendly._chat_action(app, tid)
    result = _within_deadline(lambda: _friendly._council.run_leader(app, session_id, "gpt"))
    if not isinstance(result, dict):
        result = {"status": "FAILED", "answer": ""}

    session = _friendly._council.load_session(app, session_id)
    answer = str(result.get("answer") or "").strip()
    fallback = False
    if str(result.get("status") or "") != "DONE" or not answer:
        _provider, answer = _friendly._best_available_answer(session)
        fallback = bool(answer)

    _delete_progress_message(app, tid, session)
    if answer:
        _send_final_reply(
            app,
            tid,
            session,
            "🐾 PasPuss AI",
            answer,
            _brand._user_keyboard(session_id),
        )
        try:
            _friendly._mark_delivered(app, session, fallback=fallback)
        except Exception:
            pass
        return

    _send_final_reply(
        app,
        tid,
        session,
        "🐾 PasPuss AI",
        "I couldn’t complete that answer. Please try again.",
        _brand._failure_keyboard(),
    )


def _process_question(app, tid, session_id: str, master_mode: bool) -> None:
    key = (str(tid), session_id)
    try:
        session = _friendly._council.load_session(app, session_id)
        session = _friendly._status_message(app, tid, session, _status_text(session, "asking"))
        _friendly._chat_action(app, tid)

        # Live/current questions do not need to wait for five private reviewers. Go
        # straight to the mandatory grounded live path so weather/news/prices answer fast.
        if _live._question_requires_live(str(session.get("question") or "")):
            _deliver_direct_live(app, tid, session, session_id)
            return

        _friendly._council.run_independent_answers(app, session_id)
        _brand._finish_user_from_answers(app, tid, session_id)
    except Exception:
        try:
            session = _friendly._council.load_session(app, session_id)
            _delete_progress_message(app, tid, session)
            _send_final_reply(
                app,
                tid,
                session,
                "🐾 PasPuss AI",
                "Something interrupted the reply. Please try your question again.",
                _brand._failure_keyboard(),
            )
        except Exception:
            try:
                _ui._send(app, tid, "⚠️ PasPuss AI couldn’t answer right now. Please try again.")
            except Exception:
                pass
    finally:
        with _cui._LOCK:
            _cui._INFLIGHT.discard(key)


def install() -> None:
    if getattr(_ui, "_paspuss_clean_chat_patch_installed", False):
        return

    _brand._status_text = _status_text
    _brand._finish_user_from_answers = _finish_user_from_answers
    _brand._process_question = _process_question

    _friendly._status_text = _status_text
    _friendly._status_message = _status_message
    _friendly._finish_user_from_answers = _finish_user_from_answers
    _friendly._process_question = _process_question
    _friendly._council._leader_prompt = _leader_prompt

    _cui._process_question = _process_question
    _ui._paspuss_clean_chat_patch_installed = True


install()
