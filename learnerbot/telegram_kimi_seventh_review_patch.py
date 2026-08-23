from __future__ import annotations

from . import ai_four_agent_health_patch as _health
from . import ai_health_compact_report_patch as _compact
from . import ai_ops_status as _status
from . import kimi_provider as _kimi_provider  # noqa: F401
from . import strategy_room as _strategy_room
from . import telegram_ai_council_patch as _telegram_council
from . import telegram_ai_ops_patch as _tgops
from . import telegram_grok_council_patch as _six  # noqa: F401

PROVIDERS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")
_DISPLAY_ORDER = ("gpt", "gemini", "copilot", "claude", "deepseek", "grok", "kimi")
_LABELS = {
    "gpt": "GPT",
    "gemini": "Gemini",
    "copilot": "Copilot",
    "claude": "Claude",
    "deepseek": "DeepSeek",
    "grok": "Grok",
    "kimi": "Kimi",
}

# Kimi is the seventh advisory reviewer. Deterministic LIVE/risk/capital gates
# remain outside the AI review layer and are intentionally unchanged here.
_strategy_room.PROVIDERS = PROVIDERS
_health.PROVIDERS = PROVIDERS
_compact.PROVIDERS = PROVIDERS
_compact._LABELS["kimi"] = "Kimi"

_PREV_STRATEGY_STATUS = _status.strategy_status
_PREV_TRANSITIONS = _tgops.transition_messages
_PREV_HOME = _telegram_council._home
_PREV_LEADER_KEYBOARD = _telegram_council._leader_keyboard


def _v(state: dict, name: str) -> str:
    return str((state or {}).get(name) or "WAITING").upper()


def _seven_complete(state: dict) -> bool:
    return all(_v(state, name) == "DONE" for name in _DISPLAY_ORDER)


def _icon(value: str) -> str:
    value = str(value or "WAITING").upper()
    if value == "DONE":
        return "✅"
    if value in {"INCOMPLETE", "BLOCKED", "BLOCKED_AUTH", "FAILED", "ERROR"} or value.startswith("BLOCKED_"):
        return "⚠️"
    return "⏳"


def _seven_review_summary(state: dict) -> str:
    rows = []
    completed = 0
    for provider in _DISPLAY_ORDER:
        value = _v(state, provider)
        completed += int(value == "DONE")
        rows.append(f"• {_LABELS[provider]} — {_icon(value)} {value.replace('_', ' ').title()}")
    return "\n".join(
        [
            "Review scope: All seven agents analyse the same immutable strategy cycle and evidence set.",
            "",
            "Status",
            *rows,
            "",
            f"Progress: {completed} of 7 completed",
        ]
    )


def strategy_status_seven_agent(repo_root):
    out = dict(_PREV_STRATEGY_STATUS(repo_root) or {})
    cycle = str(out.get("cycle_id") or "")
    if cycle:
        report = _status.read_json(repo_root, f"strategy/runs/{cycle}/kimi.json")
        out["kimi"] = _status._agent_status(report) if report else "WAITING"
    else:
        out.setdefault("kimi", "WAITING")
    out["seven_agent_reports_complete"] = _seven_complete(out)
    if not out.get("available"):
        out["state"] = "WAITING_FOR_SEVEN_AGENT_STRATEGY_CYCLE"
    return out


def leader_keyboard_seven_agent(session_id: str, *, include_view: bool = True):
    kb = _PREV_LEADER_KEYBOARD(session_id, include_view=include_view)
    rows = [list(row) for row in (kb.get("inline_keyboard") or [])]
    callback = f"aic:lead:kimi:{session_id}"
    if not any(any(str(button.get("callback_data") or "") == callback for button in row) for row in rows):
        insert_at = next(
            (
                idx
                for idx, row in enumerate(rows)
                if any(str(button.get("callback_data") or "").startswith("aic:view:") for button in row)
            ),
            max(0, len(rows) - 2),
        )
        rows.insert(insert_at, [{"text": "Kimi", "callback_data": callback}])
    return {"inline_keyboard": rows}


def transition_messages_seven_agent(previous: dict, current: dict) -> list[str]:
    messages = list(_PREV_TRANSITIONS(previous, current))
    ps = (previous or {}).get("strategy") or {}
    cs = (current or {}).get("strategy") or {}
    out = []
    for message in messages:
        raw = str(message)
        if raw.startswith("✅ SIX STRATEGY AGENTS COMPLETE"):
            continue
        if raw.startswith("🔬 SIX-AGENT STRATEGY REVIEW"):
            out.append("🔬 SEVEN-AGENT STRATEGY REVIEW\n\n" + _seven_review_summary(cs))
            continue
        out.append(message)
    if _seven_complete(cs) and not _seven_complete(ps):
        complete = (
            "✅ SEVEN STRATEGY AGENTS COMPLETE\n\n"
            + _seven_review_summary(cs)
            + "\n\nSelected MASTER adjudication is available or starting."
        )
        insert_at = next(
            (idx for idx, message in enumerate(out) if "MASTER STRATEGY DECISION" in str(message)),
            len(out),
        )
        out.insert(insert_at, complete)
    return out


def home_seven_agent(app, tid):
    if not _telegram_council._master(app, tid):
        return _PREV_HOME(app, tid)
    text = "\n".join(
        [
            "<b>🧠 AI COUNCIL</b>",
            "━━━━━━━━━━━━",
            "",
            "Ask one question. GPT, Gemini, Claude, Copilot, DeepSeek, Grok and Kimi are asked independently in parallel.",
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
    if getattr(_telegram_council, "_kimi_seventh_review_installed", False):
        return
    _status.strategy_status = strategy_status_seven_agent
    _tgops.transition_messages = transition_messages_seven_agent
    _telegram_council._leader_keyboard = leader_keyboard_seven_agent
    _telegram_council._home = home_seven_agent
    _tgops.AI_MASTER_COMMANDS = tuple(
        (cmd, "MASTER seven-agent strategy review status" if cmd == "aistrategy" else desc)
        for cmd, desc in _tgops.AI_MASTER_COMMANDS
    )
    _telegram_council._kimi_seventh_review_installed = True
    _tgops._seven_agent_strategy_telegram_installed = True


install()
