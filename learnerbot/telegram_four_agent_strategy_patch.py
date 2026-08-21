from __future__ import annotations

from . import ai_ops_status as _status
from . import telegram_ai_ops_patch as _tgops
from .ai_agent_identity import agent_label

_PREV_STRATEGY_STATUS = _status.strategy_status
_PREV_TRANSITIONS = _tgops.transition_messages
_AGENTS = ("gpt", "gemini", "copilot", "claude")


def _value(state: dict, name: str) -> str:
    return str((state or {}).get(name) or "WAITING").upper()


def _icon(value: str) -> str:
    value = str(value or "WAITING").upper()
    if value == "DONE":
        return "✅"
    if value in {"INCOMPLETE", "BLOCKED", "BLOCKED_AUTH", "FAILED", "ERROR"} or value.startswith("BLOCKED_"):
        return "⚠️"
    return "⏳"


def _four_complete(state: dict) -> bool:
    return all(_value(state, name) == "DONE" for name in _AGENTS)


def _agent_lines(state: dict) -> str:
    return "\n".join(
        f"{agent_label(name)} {_icon(_value(state, name))} {_value(state, name)}"
        for name in _AGENTS
    )


def strategy_status_four_agent(repo_root):
    out = dict(_PREV_STRATEGY_STATUS(repo_root) or {})
    out.setdefault("phase", "STRATEGY")
    cycle_id = str(out.get("cycle_id") or "")

    # Claude is published by a separate workflow. Read its report from the exact
    # immutable cycle so a stale/missing latest_status field cannot hide completion.
    claude = None
    if cycle_id:
        claude = _status.read_json(repo_root, f"strategy/runs/{cycle_id}/claude.json")
    if claude:
        out["claude"] = _status._agent_status(claude)
    else:
        out.setdefault("claude", "WAITING")

    out["four_agent_reports_complete"] = _four_complete(out)
    if not out.get("available"):
        out["state"] = "WAITING_FOR_FOUR_AGENT_STRATEGY_CYCLE"
    return out


def transition_messages_four_agent(previous: dict, current: dict) -> list[str]:
    messages = list(_PREV_TRANSITIONS(previous, current))
    ps = (previous or {}).get("strategy") or {}
    cs = (current or {}).get("strategy") or {}

    # Replace the legacy three-agent START message in-place so engineering and
    # master/PR notifications retain their existing ordering.
    start_text = (
        "🔬 FOUR-AGENT STRATEGY REVIEW STARTED\n"
        + _agent_lines(cs)
        + "\nAll four review the same immutable strategy cycle/evidence."
    )
    replaced: list[str] = []
    for text in messages:
        if str(text).startswith("🔬 THREE-AGENT STRATEGY REVIEW STARTED"):
            replaced.append(start_text)
            continue
        # The old completion event fires as soon as GPT/Gemini/Copilot finish.
        # Suppress it; the four-agent transition below is authoritative.
        if str(text).startswith("✅ THREE STRATEGY AGENTS COMPLETE"):
            continue
        replaced.append(text)
    messages = replaced

    current_complete = _four_complete(cs)
    previous_complete = _four_complete(ps)
    if current_complete and not previous_complete:
        complete_text = (
            "✅ FOUR STRATEGY AGENTS COMPLETE\n"
            f"{agent_label('gpt')} ✅\n"
            f"{agent_label('gemini')} ✅\n"
            f"{agent_label('copilot')} ✅\n"
            f"{agent_label('claude')} ✅\n"
            "Strategy master adjudication is available or starting."
        )
        # Put completion before a master-decision message if both become visible
        # in the same 60-second Telegram watcher tick.
        insert_at = next(
            (i for i, text in enumerate(messages) if str(text).startswith("🧠 GPT MASTER STRATEGY DECISION")),
            len(messages),
        )
        messages.insert(insert_at, complete_text)
    return messages


def strategy_text_four_agent(state: dict) -> str:
    s = (state or {}).get("strategy") or {}
    if not s.get("available"):
        return (
            "<b>🔬 FOUR-AGENT STRATEGY REVIEW</b>\n\n"
            f"{agent_label('gpt')}\n"
            f"{agent_label('gemini')}\n"
            f"{agent_label('copilot')}\n"
            f"{agent_label('claude')}\n\n"
            "Waiting for the first four-agent strategy cycle."
        )

    counts = s.get("decision_counts") or {}
    lines = [
        "<b>🔬 FOUR-AGENT STRATEGY REVIEW</b>",
        "",
        f"Cycle: <code>{_tgops._safe(s.get('cycle_id'),120)}</code>",
    ]
    lines.extend(
        f"{agent_label(name)}: {_icon(_value(s, name))} <b>{_tgops._safe(_value(s, name))}</b>"
        for name in _AGENTS
    )
    lines.append(f"All four complete: <b>{'YES' if _four_complete(s) else 'NO'}</b>")
    if s.get("master_decision_available"):
        lines += [
            "",
            f"ACCEPT {counts.get('ACCEPT',0)} | REJECT {counts.get('REJECT',0)} | DEFER {counts.get('DEFER',0)}",
        ]
    if s.get("change_pr_url"):
        lines += ["", f"Strategy change draft PR: {_tgops._safe(s.get('change_pr_url'),300)}"]
    lines += [
        "",
        "<i>New/changed strategies remain shadow-first and are never auto-deployed live by this review lane.</i>",
    ]
    return "\n".join(lines)


def install() -> None:
    if getattr(_tgops, "_four_agent_strategy_telegram_installed", False):
        return

    _status.strategy_status = strategy_status_four_agent
    # telegram_ai_ops_patch imported transition_messages by value, so replace its
    # local reference as well as the Strategy page renderer.
    _tgops.transition_messages = transition_messages_four_agent
    _tgops._strategy_text = strategy_text_four_agent
    _tgops.AI_MASTER_COMMANDS = tuple(
        (cmd, "MASTER four-agent strategy review status" if cmd == "aistrategy" else desc)
        for cmd, desc in _tgops.AI_MASTER_COMMANDS
    )
    _tgops._four_agent_strategy_telegram_installed = True


install()
