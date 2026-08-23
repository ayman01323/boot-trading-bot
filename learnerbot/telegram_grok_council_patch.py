from __future__ import annotations

from . import ai_council as _council
from . import grok_provider as _grok_provider  # noqa: F401
from . import strategy_room as _strategy_room
from . import telegram_ai_council_patch as _telegram_council

_PROVIDER = "grok"

# Strategy Room consumes the immutable independent Council answers. Add Grok to
# that evidence set without changing the existing support threshold or any LIVE
# implementation authority.
_strategy_room.PROVIDERS = tuple(dict.fromkeys((*_strategy_room.PROVIDERS, _PROVIDER)))

_PREV_LEADER_KEYBOARD = _telegram_council._leader_keyboard
_PREV_HOME = _telegram_council._home


def leader_keyboard_six_agent(session_id: str, *, include_view: bool = True):
    kb = _PREV_LEADER_KEYBOARD(session_id, include_view=include_view)
    rows = [list(row) for row in (kb.get("inline_keyboard") or [])]
    callback = f"aic:lead:grok:{session_id}"
    if not any(any(str(button.get("callback_data") or "") == callback for button in row) for row in rows):
        insert_at = next(
            (
                idx
                for idx, row in enumerate(rows)
                if any(str(button.get("callback_data") or "").startswith("aic:view:") for button in row)
            ),
            max(0, len(rows) - 2),
        )
        rows.insert(insert_at, [{"text": "Grok", "callback_data": callback}])
    return {"inline_keyboard": rows}


def home_six_agent(app, tid):
    if not _telegram_council._master(app, tid):
        return _PREV_HOME(app, tid)

    text = "\n".join(
        [
            "<b>🧠 AI COUNCIL</b>",
            "━━━━━━━━━━━━",
            "",
            "Ask one question. GPT, Gemini, Claude, Copilot, DeepSeek and Grok are asked independently in parallel.",
            "",
            "After the original answers are stored, choose any AI as Leader. The Leader sees the same original answers and produces one consolidated reply.",
            "You can then choose a different Leader for a second independent synthesis of the <b>same original answers</b>.",
            "",
            "<i>AI Council is advisory only. It cannot trade, deploy, sign, transfer assets or bypass LIVE/capital/safety controls.</i>",
        ]
    )
    kb = {
        "inline_keyboard": [
            [{"text": "✍️ Ask all AIs", "callback_data": "aic:ask"}],
            [{"text": "⬅️ Main Menu", "callback_data": "menu:home"}],
        ]
    }
    _telegram_council._ui._send(app, tid, text, kb)


def install() -> None:
    if getattr(_telegram_council, "_grok_sixth_council_installed", False):
        return
    # grok_provider already extended _council.PROVIDERS and LEADERS.
    _telegram_council._leader_keyboard = leader_keyboard_six_agent
    _telegram_council._home = home_six_agent
    _telegram_council._grok_sixth_council_installed = True


install()
