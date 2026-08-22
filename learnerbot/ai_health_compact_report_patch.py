from __future__ import annotations

import time
from pathlib import Path

from . import ai_agent_health_warning_patch as _health_warning
from . import ai_agent_health_process_dedupe_patch as _health_dedupe  # noqa: F401
from . import ai_four_agent_health_patch as _health5
from . import strategy_room as _strategy_room
from . import telegram_ai_ops_patch as _ai_ops
from . import telegram_ai_reports_menu_patch as _menu

PROVIDERS = ("gpt", "claude", "gemini", "deepseek", "copilot")
_LABELS = {
    "gpt": "GPT",
    "claude": "Claude",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "copilot": "Copilot",
}
_AI_HEALTH_HEADING = "<b>🤖 AI AGENT HEALTH</b>"
_ENGINEERING_HEADING = "<b>🛠 ENGINEERING MONITOR</b>"
_STRATEGY_HEADING = "<b>🧠 STRATEGY MONITOR</b>"
_STRATEGY_FACTORY_HEADING = "<b>🧠 STRATEGY FACTORY</b>"
_ORIGINAL_SEND_TO_CHATS = _health_warning._tg.send_to_chats

# Keep the five-agent health collectors aligned with the compact display order.
_health5.PROVIDERS = PROVIDERS


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _reason(detail: dict) -> str:
    return _health_warning._clean_reason((detail or {}).get("reason"), 320)


def _looks_like_config(reason: str) -> bool:
    r = reason.lower()
    return any(
        token in r
        for token in (
            "claude-code",
            "claude code",
            "custom model",
            "model configuration",
            "model config",
            "unsupported model",
            "base_url",
            "base url",
            "anthropic_base_url",
        )
    )


def _looks_like_auth(reason: str) -> bool:
    r = reason.lower()
    return any(
        token in r
        for token in (
            "authentication",
            "unauthorized",
            "forbidden",
            "credential",
            "api key",
            "token expired",
            "blocked_auth",
        )
    )


def _looks_like_network(reason: str) -> bool:
    r = reason.lower()
    return any(
        token in r
        for token in (
            "connection refused",
            "timed out",
            "timeout",
            "network",
            "dns",
            "unreachable",
            "connection reset",
        )
    )


def _looks_like_report_validation(reason: str) -> bool:
    r = reason.lower()
    return any(
        token in r
        for token in (
            "validation",
            "schema",
            "json",
            "parse",
            "format",
            "report is incomplete",
            "report incomplete",
            "invalid report",
        )
    )


def classify_health(lane: str, provider: str, detail: dict) -> tuple[str, str]:
    """Return a mobile-sized icon and diagnosis from real health evidence.

    Presentation only: this never changes provider state, retries, master selection,
    trading state or any execution/safety gate.
    """
    lane = str(lane or "").lower()
    provider = str(provider or "").lower()
    state = str((detail or {}).get("state") or "WAITING").upper()
    reason = _reason(detail)

    if state == "WORKING":
        return "🟢", "Working"
    if state == "WAITING":
        return "🟡", "In progress"

    if provider == "deepseek" and _looks_like_config(reason):
        return "🔴", "Model config"
    if _looks_like_auth(reason):
        return "🔴", "Authentication"
    if _looks_like_network(reason):
        return "🔴", "Provider/network"
    if provider == "gpt" or _looks_like_report_validation(reason):
        return "🟠", "Report validation"
    if provider == "claude":
        return "🟠", "Pipeline failure"
    return "🟠", "Pipeline failure"


def _lane_health(lane: str) -> dict:
    now = int(time.time())
    root = _repo_root()
    if lane == "engineering":
        return _health5._engineering_health(root, now)
    return _health5._strategy_health(root, now)


def _strategy_room_health() -> dict:
    return _strategy_room.strategy_room_agent_health(_repo_root(), int(time.time()))


def _overall_icon(health: dict | None) -> str:
    """Collapse real provider states into one lane light for the MASTER dashboard."""
    agents = (health or {}).get("agents") or {}
    states = [str((agents.get(provider) or {}).get("state") or "WAITING").upper() for provider in PROVIDERS]
    if not states:
        return "🟡"

    hard_failure = {
        "NOT_WORKING",
        "FAILED",
        "ERROR",
        "BLOCKED",
        "BLOCKED_AUTH",
        "INCOMPLETE",
    }
    if any(state in hard_failure or state.startswith("BLOCKED_") for state in states):
        return "🔴"
    if all(state == "WORKING" for state in states):
        return "🟢"
    return "🟡"


def dashboard_text(
    engineering: dict | None = None,
    strategy: dict | None = None,
    strategy_room: dict | None = None,
) -> str:
    """MASTER Telegram dashboard: one real aggregate light per AI lane."""
    engineering = engineering if engineering is not None else _lane_health("engineering")
    strategy = strategy if strategy is not None else _lane_health("strategy")
    strategy_room = strategy_room if strategy_room is not None else _strategy_room_health()
    return "\n".join(
        [
            _AI_HEALTH_HEADING,
            "│",
            f"├─ {_ENGINEERING_HEADING} {_overall_icon(engineering)}",
            f"├─ {_STRATEGY_HEADING} {_overall_icon(strategy)}",
            f"└─ {_STRATEGY_FACTORY_HEADING} {_overall_icon(strategy_room)}",
        ]
    )


def _lane_text(lane: str, health: dict | None = None) -> str:
    """Detailed drill-down retained for the dedicated audit/strategy pages."""
    health = health if health is not None else _lane_health(lane)
    heading = _ENGINEERING_HEADING if lane == "engineering" else _STRATEGY_HEADING
    agents = (health or {}).get("agents") or {}
    lines = [heading]
    for provider in PROVIDERS:
        detail = agents.get(provider) or {
            "state": "WAITING",
            "reason": f"{provider} report has not completed",
        }
        icon, status = classify_health(lane, provider, detail)
        lines.append(f"{icon} {_LABELS[provider]} — {status}")
    return "\n".join(lines)


def strategy_room_text(health: dict | None = None) -> str:
    health = health if health is not None else _strategy_room_health()
    agents = (health or {}).get("agents") or {}
    lines = [_STRATEGY_FACTORY_HEADING]
    for provider in PROVIDERS:
        detail = agents.get(provider) or {
            "state": "WAITING",
            "reason": f"{provider} has no Strategy Room mailbox result",
        }
        icon, status = classify_health("strategy_room", provider, detail)
        lines.append(f"{icon} {_LABELS[provider]} — {status}")
    return "\n".join(lines)


def engineering_text(_state: dict | None = None) -> str:
    return _lane_text("engineering")


def strategy_text(_state: dict | None = None) -> str:
    return "\n\n".join([_lane_text("strategy"), strategy_room_text()])


def warning_message(snapshot: dict) -> str:
    engineering = (snapshot or {}).get("engineering") or _lane_health("engineering")
    strategy = (snapshot or {}).get("strategy") or _lane_health("strategy")
    room = (snapshot or {}).get("strategy_room") or _strategy_room_health()
    return dashboard_text(engineering, strategy, room)


def home_text(_app, _state: dict) -> str:
    # Replace the legacy provider-heavy AI Reports home screen with the same
    # compact MASTER dashboard. Buttons still provide drill-down/control pages.
    return dashboard_text()


def _send_to_chats_with_health_html(token, chat_ids, text, **kwargs):
    if str(text or "").startswith(_AI_HEALTH_HEADING):
        kwargs.setdefault("parse_mode", "HTML")
    return _ORIGINAL_SEND_TO_CHATS(token, chat_ids, text, **kwargs)


def install() -> None:
    # Automatic unhealthy warning.
    _health_warning.warning_message = warning_message
    _health5.warning_message = warning_message

    # MASTER /aiaudit, /aistrategy and /aiupdates presentation.
    _health5._engineering_text = engineering_text
    _health5._strategy_text = strategy_text
    _ai_ops._engineering_text = engineering_text
    _ai_ops._strategy_text = strategy_text

    # MASTER AI Reports home dashboard: remove the legacy expanded provider view.
    _menu._home_text = home_text

    # Automatic health warnings use Telegram HTML only for this compact report,
    # so the headers render bold without changing formatting for other messages.
    if not getattr(_health_warning._tg, "_compact_health_html_installed", False):
        _health_warning._tg.send_to_chats = _send_to_chats_with_health_html
        _health_warning._tg._compact_health_html_installed = True

    _ai_ops._compact_ai_health_report_installed = True


install()
