from __future__ import annotations

import time

from . import ai_agent_health_warning_patch as _warning
from . import ai_health_compact_report_patch as _compact
from . import ai_health_preflight_overlay_patch as _overlay  # noqa: F401

_PREFLIGHT_KEYS = {
    "gpt": "openai",
    "claude": "anthropic",
    "deepseek": "deepseek",
    "grok": "xai",
}
_PREFLIGHT_MAX_AGE_SECONDS = 20 * 60


def _fresh_preflight() -> dict:
    value = _warning.read_json(_compact._repo_root(), "health/provider_api_preflight.json") or {}
    try:
        checked = int(value.get("checked_epoch") or 0)
    except Exception:
        return {}
    if not checked or max(0, int(time.time()) - checked) > _PREFLIGHT_MAX_AGE_SECONDS:
        return {}
    return value


def _copilot_assignment_state(lane: str, health: dict) -> str:
    root = _compact._repo_root()
    if lane == "engineering":
        source = str((health or {}).get("source_commit") or "").strip()
        if source:
            value = _warning.read_json(root, f"weekly/runs/{source}/copilot_assignment_reconciled.json") or {}
            state = str(value.get("assignment_state") or "").upper()
            if state:
                return state
    elif lane == "strategy":
        cycle = str((health or {}).get("cycle") or "").strip()
        if cycle:
            value = _warning.read_json(root, f"strategy/runs/{cycle}/copilot_assignment_reconciled.json") or {}
            state = str(value.get("assignment_state") or "").upper()
            if state:
                return state
        latest = _warning.read_json(root, "strategy/latest_status.json") or {}
        state = str(latest.get("copilot") or "").upper()
        if state == "WAITING":
            return "ASSIGNED"
        if state:
            return state
    return ""


def _review_phrase(lane: str, provider: str, detail: dict) -> tuple[str, str]:
    icon, status = _compact.classify_health(lane, provider, detail)
    label = "Engineering review" if lane == "engineering" else "Strategy review"
    return icon, f"{label} {str(status).lower()}"


def _connection_phrase(lane: str, provider: str, detail: dict, health: dict, preflight: dict) -> tuple[str, str]:
    key = _PREFLIGHT_KEYS.get(provider)
    if key and preflight:
        live = preflight.get(key) or {}
        live_state = str(live.get("state") or "").upper()
        if live_state == "WORKING":
            return "🟢", "API working"
        if live_state:
            return "🔴", "API/provider problem"

    if provider == "copilot":
        assigned = _copilot_assignment_state(lane, health)
        if assigned == "ASSIGNED":
            return "🟢", "Assigned"
        if assigned in {"BLOCKED_AUTH", "FAILED", "ERROR"}:
            return "🔴", "Assignment/auth problem"
        if assigned:
            return "🟡", "Assignment pending"

    state = str((detail or {}).get("state") or "WAITING").upper()
    if state == "WORKING":
        return "🟢", "Agent working"
    if state == "WAITING":
        return "🟡", "Agent state pending"
    review_icon, review_status = _compact.classify_health(lane, provider, detail)
    return review_icon, f"Agent {str(review_status).lower()}"


def lane_text(lane: str, health: dict | None = None) -> str:
    health = health if health is not None else _compact._lane_health(lane)
    heading = _compact._ENGINEERING_HEADING if lane == "engineering" else _compact._STRATEGY_HEADING
    agents = (health or {}).get("agents") or {}
    preflight = _fresh_preflight()
    lines = [
        heading,
        "<i>First status = agent/provider • second status = review pipeline</i>",
    ]
    for provider in _compact.PROVIDERS:
        detail = agents.get(provider) or {
            "state": "WAITING",
            "reason": f"{provider} report has not completed",
        }
        connection_icon, connection = _connection_phrase(lane, provider, detail, health or {}, preflight)
        review_icon, review = _review_phrase(lane, provider, detail)
        lines.append(
            f"{connection_icon} {_compact._LABELS[provider]} — {connection} • {review_icon} {review}"
        )
    return "\n".join(lines)


def strategy_room_text(health: dict | None = None) -> str:
    health = health if health is not None else _compact._strategy_room_health()
    agents = (health or {}).get("agents") or {}
    preflight = _fresh_preflight()
    lines = [
        _compact._STRATEGY_FACTORY_HEADING,
        "<i>Factory status is work state, not provider/API health</i>",
    ]
    for provider in _compact.PROVIDERS:
        detail = agents.get(provider) or {
            "state": "WAITING",
            "reason": f"{provider} has no Strategy Room mailbox result",
        }
        factory_icon, factory_status = _compact.classify_health("strategy_room", provider, detail)
        # Provider/API truth is shown when independently available. For Gemini and
        # Copilot there is no provider preflight entry, so do not invent one.
        key = _PREFLIGHT_KEYS.get(provider)
        if key and preflight:
            live = preflight.get(key) or {}
            if str(live.get("state") or "").upper() == "WORKING":
                lines.append(
                    f"🟢 {_compact._LABELS[provider]} — API working • {factory_icon} Factory {str(factory_status).lower()}"
                )
                continue
        lines.append(f"{factory_icon} {_compact._LABELS[provider]} — Factory {str(factory_status).lower()}")
    return "\n".join(lines)


def install() -> None:
    if getattr(_compact, "_truth_health_view_installed", False):
        return
    # dashboard_text(), engineering_text() and strategy_text() resolve these names
    # from the compact module at call time, so patching the presentation functions
    # keeps all Telegram health entry points consistent without changing health state.
    _compact._lane_text = lane_text
    _compact.strategy_room_text = strategy_room_text
    _compact._truth_health_view_installed = True


install()
