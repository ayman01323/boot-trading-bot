from __future__ import annotations

import html
import threading
import time
from pathlib import Path

from . import ai_council as _council
from . import strategy_room as _room
from . import telegram_ai_council_patch as _cui
from . import telegram_paspuss_clean_chat_patch as _clean
from . import telegram_ui as _ui

_PREV_MENU_KEYBOARD = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update
_PENDING: dict[str, str] = {}
_INFLIGHT: set[tuple[str, str]] = set()
_LOCK = threading.Lock()


def _master(app, tid) -> bool:
    try:
        return bool(_cui._master(app, tid))
    except Exception:
        return False


def _room_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "💬 Ask all agents", "callback_data": "sr:ask"}],
        [
            {"text": "🩺 Agent health", "callback_data": "sr:health"},
            {"text": "🛡️ Loss monitor", "callback_data": "sr:loss"},
        ],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def _result_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "💬 Ask Strategy Room again", "callback_data": "sr:ask"}],
        [
            {"text": "🩺 Agent health", "callback_data": "sr:health"},
            {"text": "🛡️ Loss monitor", "callback_data": "sr:loss"},
        ],
        [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
    ]}


def menu_keyboard(app=None, chat_id=None):
    keyboard = _PREV_MENU_KEYBOARD(app, chat_id)
    if app is None or chat_id is None or not _master(app, chat_id):
        return keyboard
    rows = list(keyboard.get("inline_keyboard") or [])
    if not any(
        str(button.get("callback_data") or "").startswith("sr:")
        for row in rows
        for button in row
    ):
        rows.insert(1 if rows else 0, [{"text": "🧠 Strategy Room", "callback_data": "sr:home"}])
    return {"inline_keyboard": rows}


def _home(app, tid) -> None:
    _ui._send(
        app,
        tid,
        "<b>🧠 STRATEGY ROOM</b>\n\n"
        "Ask one trading-bot strategy or engineering question. GPT, Claude, Gemini, DeepSeek and Copilot answer independently, then GPT reviews the available replies and gives one final decision.\n\n"
        "If GPT finds a justified LOW/MEDIUM-risk Strategy Lab or SHADOW change, it may queue a bounded <b>draft PR only</b>. Anything involving LIVE trading, thresholds, stop-loss/take-profit, risk, capital, CSV settings, wallets/signing, deployment or auto-merge requires your explicit approval.\n\n"
        "No Strategy Room question can itself place a trade or move assets.",
        _room_keyboard(),
    )


def _prompt(app, tid) -> None:
    _PENDING[str(tid)] = "question"
    _ui._send(
        app,
        tid,
        "<b>🧠 Strategy Room</b>\n\nSend the question you want all five agents to review in one message.\n\nSend <code>/cancel</code> to cancel.",
        {"inline_keyboard": [[{"text": "Cancel", "callback_data": "sr:cancel"}]]},
    )


def _health_text(app) -> str:
    root = Path(__file__).resolve().parents[1]
    snapshot = _room.strategy_room_agent_health(root)
    agents = snapshot.get("agents") or {}
    labels = {
        "gpt": "GPT",
        "claude": "Claude",
        "gemini": "Gemini",
        "deepseek": "DeepSeek",
        "copilot": "Copilot",
    }
    lines = [
        "<b>🧠 STRATEGY ROOM — AGENT HEALTH</b>",
        "",
        "Health reflects each agent's latest Strategy Room mailbox/session result, not its general provider account status.",
        "",
    ]
    for provider in _room.PROVIDERS:
        row = agents.get(provider) or {}
        state = str(row.get("state") or "WAITING").upper()
        if state == "WORKING":
            icon, status = "🟢", "Working"
        elif state == "WAITING":
            icon, status = "🟡", "Waiting / no recent room reply"
        else:
            icon, status = "🔴", "Failed"
        age = row.get("age_seconds")
        age_text = ""
        if isinstance(age, int):
            if age < 60:
                age_text = f" • {age}s ago"
            elif age < 3600:
                age_text = f" • {age // 60}m ago"
            else:
                age_text = f" • {age // 3600}h ago"
        lines.append(f"{icon} <b>{labels[provider]}</b> — {status}{age_text}")
        if state == "FAILED":
            reason = html.escape(str(row.get("reason") or "Provider did not complete the latest Strategy Room request")[:180])
            lines.append(f"   <code>{reason}</code>")
    return "\n".join(lines)


def _save_leader_result(app, session_id: str, *, answer: str, rc: int, error: str, action: str, task: str) -> dict:
    session = _council.load_session(app, session_id)
    leaders = dict(session.get("leaders") or {})
    leaders["gpt"] = {
        "status": "DONE" if rc == 0 and answer else "FAILED",
        "answer": answer[:_council.MAX_AGENT_ANSWER_CHARS] if answer else "",
        "error": str(error or "")[:1200],
        "return_code": int(rc),
        "created_epoch": int(time.time()),
        "strategy_room_action": action,
        "strategy_room_task": task,
    }
    session["leaders"] = leaders
    session["status"] = "LEADER_READY" if leaders["gpt"]["status"] == "DONE" else "ANSWERS_READY"
    session["updated_epoch"] = int(time.time())
    return _council.save_session(app, session)


def _worker(app, tid, session_id: str) -> None:
    key = (str(tid), session_id)
    try:
        _ui._send(
            app,
            tid,
            "🧠 <b>Strategy Room is reviewing your question…</b>\n\n"
            "Asking GPT, Claude, Gemini, DeepSeek and Copilot independently.",
        )
        session = _council.run_independent_answers(app, session_id)
        valid = sum(
            1 for row in (session.get("answers") or {}).values()
            if str((row or {}).get("status") or "") == "DONE"
        )
        if valid == 0:
            _ui._send(
                app,
                tid,
                "⚠️ <b>Strategy Room</b>\n\nNone of the five agents returned a usable review. No implementation was queued.",
                _result_keyboard(),
            )
            return

        _ui._send(
            app,
            tid,
            f"🧠 <b>Strategy Room</b>\n\n{valid}/5 agent replies received. GPT is now reviewing the available evidence and deciding whether any bounded change is justified.",
        )
        prompt = _room.build_gpt_leader_prompt(session)
        started = time.monotonic()
        rc, raw, err = _council.call_provider("gpt", prompt)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        visible, action, task = _room.parse_gpt_leader_output(raw if rc == 0 else "")
        if rc != 0 or not visible:
            # A failed GPT leader is not permission to implement a minority opinion.
            _save_leader_result(
                app, session_id, answer="", rc=rc, error=err or "GPT leader did not complete",
                action="NONE", task="",
            )
            _ui._send(
                app,
                tid,
                "⚠️ <b>Strategy Room</b>\n\nThe independent reviews completed, but GPT could not complete the leader decision. No implementation was queued.",
                _result_keyboard(),
            )
            return

        session = _save_leader_result(
            app, session_id, answer=visible, rc=rc, error="", action=action, task=task,
        )
        note = "\n\n<b>Implementation:</b> No change queued."
        if action == "DRAFT_SHADOW_CHANGE":
            try:
                queued = _room.queue_draft_shadow_change(
                    app,
                    task=task,
                    question=str(session.get("question") or ""),
                    session_id=session_id,
                    requested_by=tid,
                    support_count=valid,
                )
                note = (
                    "\n\n<b>Implementation:</b> 🛠 GPT queued a bounded Strategy Lab/SHADOW draft implementation "
                    f"(request <code>{html.escape(str(queued.get('nonce') or ''))}</code>). "
                    "The GitHub worker may open a draft PR after allow-list and test checks; it cannot merge or deploy it."
                )
            except Exception as exc:
                note = (
                    "\n\n<b>Implementation:</b> ⚠️ GPT recommended a bounded draft change, but it was not queued: "
                    f"<code>{html.escape(str(exc)[:240])}</code>"
                )
        elif action == "HUMAN_APPROVAL_REQUIRED":
            note = (
                "\n\n<b>Implementation:</b> ⚠️ GPT says the recommended change affects a protected LIVE/risk/capital/threshold/"
                "wallet/deployment area. Nothing was changed or queued; your explicit approval is required."
            )

        # Keep duration as auditable metadata without cluttering the user-facing review.
        session = _council.load_session(app, session_id)
        session.setdefault("leaders", {}).setdefault("gpt", {})["duration_ms"] = elapsed_ms
        _council.save_session(app, session)
        _clean._send_final_reply(
            app,
            tid,
            session,
            "🧠 Strategy Room — GPT decision",
            visible + note,
            _result_keyboard(),
        )
    except Exception as exc:
        _ui._send(
            app,
            tid,
            "⚠️ <b>Strategy Room interrupted</b>\n\n"
            f"<code>{html.escape(str(exc)[:320])}</code>\n\nNo implementation was queued.",
            _result_keyboard(),
        )
    finally:
        with _LOCK:
            _INFLIGHT.discard(key)


def _start_question(app, tid, question: str) -> None:
    session = _council.create_session(app, tid, question, mode="strategy_room")
    session_id = str(session.get("session_id") or "")
    key = (str(tid), session_id)
    with _LOCK:
        _INFLIGHT.add(key)
    threading.Thread(
        target=_worker,
        args=(app, tid, session_id),
        name=f"strategy-room-{session_id}",
        daemon=True,
    ).start()


def _handle_pending(app, tid, text: str) -> bool:
    if _PENDING.get(str(tid)) != "question":
        return False
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
    if cmd:
        _PENDING.pop(str(tid), None)
        if cmd == "/cancel":
            _ui._send(app, tid, "✅ Strategy Room question cancelled.", _room_keyboard())
            return True
        return False
    _PENDING.pop(str(tid), None)
    if not text.strip():
        _prompt(app, tid)
        return True
    try:
        _start_question(app, tid, text)
    except Exception as exc:
        _ui._send(
            app, tid,
            f"⚠️ <b>Strategy Room</b>\n\n<code>{html.escape(str(exc)[:300])}</code>",
            _room_keyboard(),
        )
    return True


def handle_update(app, update):
    cb = update.get("callback_query") or {}
    cb_tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
    data = str(cb.get("data") or "")
    if cb_tid is not None and data.startswith("sr:"):
        if not _master(app, cb_tid):
            _cui._answer_callback(app, cb, "MASTER only")
            return
        _cui._answer_callback(app, cb)
        if data == "sr:home":
            _home(app, cb_tid)
        elif data == "sr:ask":
            _prompt(app, cb_tid)
        elif data == "sr:health":
            _ui._send(app, cb_tid, _health_text(app), _room_keyboard())
        elif data == "sr:loss":
            _ui._send(app, cb_tid, _room.loss_monitor_plan(app, cb_tid), _room_keyboard())
        elif data == "sr:cancel":
            _PENDING.pop(str(cb_tid), None)
            _ui._send(app, cb_tid, "✅ Strategy Room question cancelled.", _room_keyboard())
        return

    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and _PENDING.get(str(tid)) == "question":
        if not _master(app, tid):
            _PENDING.pop(str(tid), None)
            return _PREV_HANDLE_UPDATE(app, update)
        if _handle_pending(app, tid, text):
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    if getattr(_ui, "_telegram_strategy_room_patch_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._telegram_strategy_room_patch_installed = True


install()
