from __future__ import annotations

from . import ai_council as _council
from . import ai_four_agent_health_patch as _health
from . import ai_health_compact_report_patch as _compact
from . import ai_ops_status as _status
from . import grok_provider as _grok_provider  # noqa: F401
from . import strategy_room as _strategy_room
from . import telegram_ai_council_patch as _telegram_council
from . import telegram_ai_ops_patch as _tgops
from . import telegram_ai_reports_menu_patch as _menu

_PROVIDER = "grok"
PROVIDERS = ("gpt", "claude", "gemini", "deepseek", "grok", "copilot")
_DISPLAY_ORDER = ("gpt", "gemini", "copilot", "claude", "deepseek", "grok")
_LABELS = {
    "gpt": "GPT",
    "gemini": "Gemini",
    "copilot": "Copilot",
    "claude": "Claude",
    "deepseek": "DeepSeek",
    "grok": "Grok",
}

# Strategy Room and the scheduled health collectors use the same six-provider set.
# This is presentation/orchestration only; deterministic LIVE gates stay outside AI.
_strategy_room.PROVIDERS = PROVIDERS
_health.PROVIDERS = PROVIDERS
_compact.PROVIDERS = PROVIDERS
_compact._LABELS["grok"] = "Grok"

_PREV_LEADER_KEYBOARD = _telegram_council._leader_keyboard
_PREV_HOME = _telegram_council._home
_PREV_ENGINEERING_STATUS = _status.engineering_status
_PREV_STRATEGY_STATUS = _status.strategy_status
_PREV_CONTROL_KEYBOARD = _menu._control_keyboard
_PREV_TRANSITIONS = _tgops.transition_messages


def _v(state: dict, name: str) -> str:
    return str((state or {}).get(name) or "WAITING").upper()


def _six_complete(state: dict) -> bool:
    return all(_v(state, name) == "DONE" for name in _DISPLAY_ORDER)


def _icon(value: str) -> str:
    value = str(value or "WAITING").upper()
    if value == "DONE":
        return "✅"
    if value in {"INCOMPLETE", "BLOCKED", "BLOCKED_AUTH", "FAILED", "ERROR"} or value.startswith("BLOCKED_"):
        return "⚠️"
    return "⏳"


def _six_review_summary(state: dict) -> str:
    rows = []
    completed = 0
    for provider in _DISPLAY_ORDER:
        value = _v(state, provider)
        completed += int(value == "DONE")
        rows.append(f"• {_LABELS[provider]} — {_icon(value)} {value.replace('_', ' ').title()}")
    return "\n".join(
        [
            "Review scope: All six agents analyse the same immutable strategy cycle and evidence set.",
            "",
            "Status",
            *rows,
            "",
            f"Progress: {completed} of 6 completed",
        ]
    )


def engineering_status_six_agent(repo_root):
    out = dict(_PREV_ENGINEERING_STATUS(repo_root) or {})
    source = str(out.get("source_commit") or "")
    if source:
        report = _status.read_json(repo_root, f"weekly/runs/{source}/grok.json")
        out["grok"] = _status._agent_status(report) if report else "WAITING"
    else:
        out.setdefault("grok", "WAITING")
    out["six_agent_reports_complete"] = _six_complete(out)
    return out


def strategy_status_six_agent(repo_root):
    out = dict(_PREV_STRATEGY_STATUS(repo_root) or {})
    cycle = str(out.get("cycle_id") or "")
    if cycle:
        report = _status.read_json(repo_root, f"strategy/runs/{cycle}/grok.json")
        out["grok"] = _status._agent_status(report) if report else "WAITING"
    else:
        out.setdefault("grok", "WAITING")
    out["six_agent_reports_complete"] = _six_complete(out)
    if not out.get("available"):
        out["state"] = "WAITING_FOR_SIX_AGENT_STRATEGY_CYCLE"
    return out


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


def control_keyboard_six_agent(value: dict) -> dict:
    kb = _PREV_CONTROL_KEYBOARD(value)
    rows = [list(row) for row in (kb.get("inline_keyboard") or [])]
    for lane in ("strategy", "engineering"):
        callback = f"aicfg:master:{lane}:grok"
        if any(any(str(button.get("callback_data") or "") == callback for button in row) for row in rows):
            continue
        active = str(value.get(lane + "_master") or "auto") == "grok"
        button = {"text": ("✅ " if active else "") + "Grok", "callback_data": callback}
        anchor = f"aicfg:master:{lane}:deepseek"
        pos = next(
            (idx + 1 for idx, row in enumerate(rows) if any(str(b.get("callback_data") or "") == anchor for b in row)),
            len(rows),
        )
        rows.insert(pos, [button])
    return {"inline_keyboard": rows}


def transition_messages_six_agent(previous: dict, current: dict) -> list[str]:
    messages = list(_PREV_TRANSITIONS(previous, current))
    ps = (previous or {}).get("strategy") or {}
    cs = (current or {}).get("strategy") or {}
    out = []
    for message in messages:
        raw = str(message)
        if raw.startswith("✅ FIVE STRATEGY AGENTS COMPLETE"):
            # The legacy five-agent layer can become complete before Grok finishes.
            continue
        if raw.startswith("🔬 FIVE-AGENT STRATEGY REVIEW"):
            out.append("🔬 SIX-AGENT STRATEGY REVIEW\n\n" + _six_review_summary(cs))
            continue
        out.append(message)
    if _six_complete(cs) and not _six_complete(ps):
        complete = (
            "✅ SIX STRATEGY AGENTS COMPLETE\n\n"
            + _six_review_summary(cs)
            + "\n\nSelected MASTER adjudication is available or starting."
        )
        insert_at = next(
            (idx for idx, message in enumerate(out) if "MASTER STRATEGY DECISION" in str(message)),
            len(out),
        )
        out.insert(insert_at, complete)
    return out


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

    # Grok becomes a normal Council member/Leader and a normal scheduled health row.
    _telegram_council._leader_keyboard = leader_keyboard_six_agent
    _telegram_council._home = home_six_agent
    _menu._PROVIDER_LABELS["grok"] = "Grok"
    _menu._control_keyboard = control_keyboard_six_agent
    _status.engineering_status = engineering_status_six_agent
    _status.strategy_status = strategy_status_six_agent
    _tgops.transition_messages = transition_messages_six_agent

    _tgops.AI_MASTER_COMMANDS = tuple(
        (cmd, "MASTER six-agent strategy review status" if cmd == "aistrategy" else desc)
        for cmd, desc in _tgops.AI_MASTER_COMMANDS
    )
    _telegram_council._grok_sixth_council_installed = True
    _tgops._six_agent_strategy_telegram_installed = True


install()
