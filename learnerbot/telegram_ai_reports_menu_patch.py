from __future__ import annotations

import copy
import html

from . import telegram_ai_ops_patch as _ai
from . import telegram_sibot_patch as _sibot_ui
from . import telegram_ui as _ui
from .ai_ops_status import snapshot_for_display
from .user_registry import is_master

_PREV_MENU = _ui.menu_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update


def _is_master(app, chat_id) -> bool:
    try:
        return bool(app is not None and chat_id is not None and is_master(app.csv_dir, chat_id))
    except Exception:
        return False


def _insert_ai_reports_button(rows: list[list[dict]]) -> list[list[dict]]:
    if any(any(str(b.get("callback_data") or "") == "menu:aiops" for b in row) for row in rows):
        return rows
    insert_at = len(rows)
    for i, row in enumerate(rows):
        callbacks = {str(b.get("callback_data") or "") for b in row}
        if "menu:report" in callbacks:
            insert_at = i
            break
    rows.insert(insert_at, [{"text": "🤖 AI Reports", "callback_data": "menu:aiops"}])
    return rows


def menu_keyboard(app=None, chat_id=None):
    kb = copy.deepcopy(_PREV_MENU(app, chat_id))
    if not _is_master(app, chat_id):
        return kb
    rows = list(kb.get("inline_keyboard") or [])
    return {"inline_keyboard": _insert_ai_reports_button(rows)}


def _ai_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🧪 Engineering Audit", "callback_data": "aiops:audit"},
                {"text": "🧠 GPT Decisions", "callback_data": "aiops:decision"},
            ],
            [
                {"text": "🔬 Strategy Review", "callback_data": "aiops:strategy"},
                {"text": "📋 All AI Updates", "callback_data": "aiops:updates"},
            ],
            [
                {"text": "🔄 Refresh", "callback_data": "menu:aiops"},
                {"text": "⬅️ Main Menu", "callback_data": "menu:home"},
            ],
        ]
    }


def _home_text(state: dict) -> str:
    eng = (state or {}).get("engineering") or {}
    strategy = (state or {}).get("strategy") or {}

    def mark(value):
        value = str(value or "WAITING").upper()
        if value == "DONE":
            return "✅"
        if value == "INCOMPLETE":
            return "⚠️"
        return "⏳"

    lines = [
        "<b>🤖 AI REPORTS &amp; OPERATIONS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "<b>ENGINEERING</b>",
    ]
    if eng.get("available"):
        lines += [
            f"GPT {mark(eng.get('gpt'))}  Gemini {mark(eng.get('gemini'))}  Copilot {mark(eng.get('copilot'))}",
            f"Master decision: <b>{html.escape(str(eng.get('master_status') or 'WAITING'))}</b>",
        ]
    else:
        lines.append("⏳ No completed/published engineering cycle yet.")

    lines += ["", "<b>STRATEGY — HOURLY</b>"]
    if strategy.get("available"):
        lines += [
            f"GPT {mark(strategy.get('gpt'))}  Gemini {mark(strategy.get('gemini'))}  Copilot {mark(strategy.get('copilot'))}",
            f"Cycle: <code>{html.escape(str(strategy.get('cycle_id') or '')[:80])}</code>",
            f"Master decision: <b>{'AVAILABLE' if strategy.get('master_decision_available') else 'WAITING'}</b>",
        ]
    else:
        lines.append("⏳ Waiting for the first published hourly three-agent strategy cycle.")

    if not state.get("fetch_ok", True):
        lines += ["", "⚠️ Latest ai-reviews fetch failed; showing cached state."]

    lines += [
        "",
        "Tap a report below. GPT ACCEPT / REJECT / DEFER detail is under <b>GPT Decisions</b>.",
    ]
    return "\n".join(lines)


def _answer(app, cb, text=""):
    cqid = (cb or {}).get("id")
    if not cqid:
        return
    try:
        _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
    except Exception:
        pass


def _render(app, tid, text: str, cb=None):
    _sibot_ui._render(app, tid, text, _ai_keyboard(), cb)


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data == "menu:aiops" or data.startswith("aiops:"):
            if not _is_master(app, tid):
                _answer(app, cb, "MASTER only")
                return
            _answer(app, cb)
            state = snapshot_for_display(_ai._repo_root())
            if data == "aiops:audit":
                body = _ai._engineering_text(state)
            elif data == "aiops:decision":
                body = _ai._decision_text(state)
            elif data == "aiops:strategy":
                body = _ai._strategy_text(state)
            elif data == "aiops:updates":
                body = _ai._combined_text(state)
            else:
                body = _home_text(state)
            _render(app, tid, body, cb)
            return
    return _PREV_HANDLE_UPDATE(app, update)


def install():
    if getattr(_ui, "_telegram_ai_reports_menu_patch_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._telegram_ai_reports_menu_patch_installed = True


install()
