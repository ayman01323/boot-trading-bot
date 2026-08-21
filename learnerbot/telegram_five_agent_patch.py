from __future__ import annotations

from . import ai_four_agent_health_patch as _health5
from . import ai_ops_status as _status
from . import telegram_ai_ops_patch as _tgops
from . import telegram_ai_reports_menu_patch as _menu
from . import telegram_four_agent_strategy_patch as _strategy4

PROVIDERS = ("gpt", "claude", "gemini", "deepseek", "copilot")
_DISPLAY_ORDER = ("gpt", "gemini", "copilot", "claude", "deepseek")
_DISPLAY_LABELS = {
    "gpt": "GPT",
    "gemini": "Gemini",
    "copilot": "Copilot",
    "claude": "Claude",
    "deepseek": "DeepSeek",
}

# The existing health functions read the provider tuple dynamically. Expanding it
# preserves their safety/age logic while making DeepSeek an independent fifth row.
_health5.PROVIDERS = PROVIDERS

_PREV_WARNING = _health5.warning_message
_PREV_OPS_TEXT = _health5._ops_text
_PREV_ENGINEERING_STATUS = _status.engineering_status
_PREV_STRATEGY_STATUS = _status.strategy_status
_PREV_TRANSITIONS = _tgops.transition_messages
_PREV_HOME_TEXT = _menu._home_text
_PREV_CONTROL_KEYBOARD = _menu._control_keyboard


def _working_report(report: dict | None) -> str:
    return _status._agent_status(report)


def engineering_status_five_agent(repo_root):
    out = dict(_PREV_ENGINEERING_STATUS(repo_root) or {})
    source = str(out.get("source_commit") or "")
    if source:
        root = f"weekly/runs/{source}"
        for provider in ("claude", "deepseek"):
            report = _status.read_json(repo_root, f"{root}/{provider}.json")
            out[provider] = _working_report(report) if report else "WAITING"
    else:
        out.setdefault("claude", "WAITING")
        out.setdefault("deepseek", "WAITING")
    out["five_agent_reports_complete"] = all(str(out.get(x) or "WAITING").upper() == "DONE" for x in _DISPLAY_ORDER)
    return out


def strategy_status_five_agent(repo_root):
    out = dict(_PREV_STRATEGY_STATUS(repo_root) or {})
    cycle = str(out.get("cycle_id") or "")
    if cycle:
        report = _status.read_json(repo_root, f"strategy/runs/{cycle}/deepseek.json")
        out["deepseek"] = _working_report(report) if report else "WAITING"
    else:
        out.setdefault("deepseek", "WAITING")
    out["five_agent_reports_complete"] = all(str(out.get(x) or "WAITING").upper() == "DONE" for x in _DISPLAY_ORDER)
    if not out.get("available"):
        out["state"] = "WAITING_FOR_FIVE_AGENT_STRATEGY_CYCLE"
    return out


def warning_message_five_agent(snapshot: dict) -> str:
    text = _PREV_WARNING(snapshot)
    text = text.replace("/4 valid report(s)", "/5 valid report(s)")
    text = text.replace(
        "selected MASTER → GPT → Claude → Gemini → other available agent.",
        "selected MASTER → GPT → Claude → Gemini → DeepSeek → other available agent.",
    )
    return text


def ops_text_five_agent(lane: str, state: dict) -> str:
    text = _PREV_OPS_TEXT(lane, state)
    return text.replace("/4</b>", "/5</b>")


def _v(state: dict, name: str) -> str:
    return str((state or {}).get(name) or "WAITING").upper()


def _icon(value: str) -> str:
    value = str(value or "WAITING").upper()
    if value == "DONE":
        return "✅"
    if value in {"INCOMPLETE", "BLOCKED", "BLOCKED_AUTH", "FAILED", "ERROR"} or value.startswith("BLOCKED_"):
        return "⚠️"
    return "⏳"


def _status_label(value: str) -> str:
    value = str(value or "WAITING").upper()
    labels = {
        "DONE": "Completed",
        "WAITING": "Waiting",
        "BLOCKED_AUTH": "Blocked: Authentication",
        "BLOCKED": "Blocked",
        "INCOMPLETE": "Incomplete",
        "FAILED": "Failed",
        "ERROR": "Error",
    }
    if value in labels:
        return labels[value]
    if value.startswith("BLOCKED_"):
        reason = value.removeprefix("BLOCKED_").replace("_", " ").title()
        return f"Blocked: {reason}"
    return value.replace("_", " ").title()


def _five_complete(state: dict) -> bool:
    return all(_v(state, name) == "DONE" for name in _DISPLAY_ORDER)


def _completed_count(state: dict) -> int:
    return sum(1 for name in _DISPLAY_ORDER if _v(state, name) == "DONE")


def _five_lines(state: dict, *, html: bool = False) -> str:
    rows = []
    for name in _DISPLAY_ORDER:
        value = _v(state, name)
        label = _DISPLAY_LABELS[name]
        status = _status_label(value)
        if html:
            rows.append(f"• {label} — {_icon(value)} <b>{_tgops._safe(status,120)}</b>")
        else:
            rows.append(f"• {label} — {_icon(value)} {status}")
    return "\n".join(rows)


def _review_summary(state: dict, *, html: bool = False, include_cycle: bool = False) -> str:
    completed = _completed_count(state)
    if html:
        lines = [
            "Review scope: All five agents are analysing the same immutable strategy cycle and evidence set.",
        ]
        if include_cycle and state.get("cycle_id"):
            lines.append(f"Cycle: <code>{_tgops._safe(state.get('cycle_id'),120)}</code>")
        lines += [
            "",
            "<b>Status</b>",
            _five_lines(state, html=True),
            "",
            f"Progress: <b>{completed} of 5 completed</b>",
        ]
        return "\n".join(lines)

    lines = [
        "Review scope: All five agents are analysing the same immutable strategy cycle and evidence set.",
    ]
    if include_cycle and state.get("cycle_id"):
        lines.append(f"Cycle: {state.get('cycle_id')}")
    lines += [
        "",
        "Status",
        _five_lines(state),
        "",
        f"Progress: {completed} of 5 completed",
    ]
    return "\n".join(lines)


def transition_messages_five_agent(previous: dict, current: dict) -> list[str]:
    messages = list(_PREV_TRANSITIONS(previous, current))
    ps = (previous or {}).get("strategy") or {}
    cs = (current or {}).get("strategy") or {}
    replaced = []
    for text in messages:
        raw = str(text)
        if raw.startswith("🔬 FOUR-AGENT STRATEGY REVIEW STARTED") or raw.startswith("🔬 THREE-AGENT STRATEGY REVIEW STARTED"):
            replaced.append(
                "🔬 FIVE-AGENT STRATEGY REVIEW\n\n"
                + _review_summary(cs)
            )
            continue
        if raw.startswith("✅ FOUR STRATEGY AGENTS COMPLETE") or raw.startswith("✅ THREE STRATEGY AGENTS COMPLETE"):
            continue
        replaced.append(text)
    messages = replaced
    if _five_complete(cs) and not _five_complete(ps):
        complete = (
            "✅ FIVE STRATEGY AGENTS COMPLETE\n\n"
            + _review_summary(cs)
            + "\n\nStrategy master adjudication is available or starting."
        )
        insert_at = next((i for i,t in enumerate(messages) if str(t).startswith("🧠 GPT MASTER STRATEGY DECISION")), len(messages))
        messages.insert(insert_at, complete)
    return messages


def strategy_text_five_agent(state: dict) -> str:
    s = (state or {}).get("strategy") or {}
    if not s.get("available"):
        return (
            "<b>🔬 FIVE-AGENT STRATEGY REVIEW</b>\n\n"
            "Review scope: All five agents analyse the same immutable strategy cycle and evidence set.\n\n"
            "<b>Status</b>\n"
            + _five_lines(s, html=True)
            + "\n\nProgress: <b>0 of 5 completed</b>"
        )
    counts = s.get("decision_counts") or {}
    lines = [
        "<b>🔬 FIVE-AGENT STRATEGY REVIEW</b>",
        "",
        _review_summary(s, html=True, include_cycle=True),
    ]
    if s.get("master_decision_available"):
        lines += [
            "",
            f"Decision summary: ACCEPT {counts.get('ACCEPT',0)} | REJECT {counts.get('REJECT',0)} | DEFER {counts.get('DEFER',0)}",
        ]
    if s.get("change_pr_url"):
        lines += ["", f"Strategy change draft PR: {_tgops._safe(s.get('change_pr_url'),300)}"]
    lines += ["", "<i>New/changed strategies remain shadow-first and are never auto-deployed live by this review lane.</i>"]
    return "\n".join(lines)


def control_keyboard_five_agent(value: dict) -> dict:
    kb = _PREV_CONTROL_KEYBOARD(value)
    rows = list(kb.get("inline_keyboard") or [])
    for lane in ("strategy", "engineering"):
        callback = f"aicfg:master:{lane}:deepseek"
        if any(any(str(b.get('callback_data') or '') == callback for b in row) for row in rows):
            continue
        active = str(value.get(lane + "_master") or "auto") == "deepseek"
        button = {"text": ("✅ " if active else "") + "DeepSeek", "callback_data": callback}
        anchor = f"aicfg:master:{lane}:claude"
        pos = next((i+1 for i,row in enumerate(rows) if any(str(b.get('callback_data') or '') == anchor for b in row)), None)
        if pos is not None:
            rows.insert(pos, [button])
    return {"inline_keyboard": rows}


def home_text_five_agent(app, state: dict) -> str:
    text = _PREV_HOME_TEXT(app, state)
    eng = (state or {}).get("engineering") or {}
    strategy = (state or {}).get("strategy") or {}
    old_eng = f"GPT {_menu._mark(eng.get('gpt'))}  Gemini {_menu._mark(eng.get('gemini'))}  Copilot {_menu._mark(eng.get('copilot'))}  Claude {_menu._mark(eng.get('claude'))}"
    new_eng = old_eng + f"  DeepSeek {_menu._mark(eng.get('deepseek'))}"
    old_strategy = f"GPT {_menu._mark(strategy.get('gpt'))}  Gemini {_menu._mark(strategy.get('gemini'))}  Copilot {_menu._mark(strategy.get('copilot'))}  Claude {_menu._mark(strategy.get('claude'))}"
    new_strategy = old_strategy + f"  DeepSeek {_menu._mark(strategy.get('deepseek'))}"
    return text.replace(old_eng, new_eng).replace(old_strategy, new_strategy)


def install() -> None:
    _menu._PROVIDER_LABELS["deepseek"] = "DeepSeek"
    _menu._control_keyboard = control_keyboard_five_agent
    _menu._home_text = home_text_five_agent

    _status.engineering_status = engineering_status_five_agent
    _status.strategy_status = strategy_status_five_agent
    _tgops.transition_messages = transition_messages_five_agent
    _tgops._strategy_text = strategy_text_five_agent

    _health5.warning_message = warning_message_five_agent
    _health5._ops_text = ops_text_five_agent
    _health5._health.warning_message = warning_message_five_agent
    _health5._ai._engineering_text = _health5._engineering_text
    _health5._ai._strategy_text = _health5._strategy_text

    _tgops.AI_MASTER_COMMANDS = tuple(
        (cmd, "MASTER five-agent strategy review status" if cmd == "aistrategy" else desc)
        for cmd,desc in _tgops.AI_MASTER_COMMANDS
    )
    _tgops._five_agent_strategy_telegram_installed = True


install()

# Final health presentation layer: compact ENGINEERING/STRATEGY status rows.
from . import ai_health_compact_report_patch as _compact_health  # noqa: F401,E402
