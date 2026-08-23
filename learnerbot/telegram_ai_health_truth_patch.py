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
    """Return provider preflight evidence with freshness metadata.

    Stale evidence is deliberately retained so Telegram can say "last known"
    rather than incorrectly substituting a review-pipeline failure for provider
    health. Review state and provider/API state must remain independent.
    """
    value = _warning.read_json(_compact._repo_root(), "health/provider_api_preflight.json") or {}
    try:
        checked = int(value.get("checked_epoch") or 0)
    except Exception:
        checked = 0
    age = max(0, int(time.time()) - checked) if checked else None
    value["_truth_age_seconds"] = age
    value["_truth_stale"] = bool(age is None or age > _PREFLIGHT_MAX_AGE_SECONDS)
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
    if key:
        live = (preflight or {}).get(key) or {}
        live_state = str(live.get("state") or "").upper()
        stale = bool((preflight or {}).get("_truth_stale", True))
        if live_state == "WORKING":
            if stale:
                return "🟡", "API check stale (last working)"
            return "🟢", "API working"
        if live_state:
            if stale:
                return "🟡", "API check stale (last problem)"
            return "🔴", "API/provider problem"
        return "🟡", "API status unavailable"

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
    # Providers without an independent API preflight (currently Gemini) still
    # use their agent report. Mapped providers above never inherit review state.
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
    stale = bool((preflight or {}).get("_truth_stale", True))
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
        key = _PREFLIGHT_KEYS.get(provider)
        if key:
            live = (preflight or {}).get(key) or {}
            live_state = str(live.get("state") or "").upper()
            if live_state == "WORKING" and not stale:
                lines.append(
                    f"🟢 {_compact._LABELS[provider]} — API working • {factory_icon} Factory {str(factory_status).lower()}"
                )
                continue
            if live_state == "WORKING" and stale:
                lines.append(
                    f"🟡 {_compact._LABELS[provider]} — API check stale (last working) • {factory_icon} Factory {str(factory_status).lower()}"
                )
                continue
        lines.append(f"{factory_icon} {_compact._LABELS[provider]} — Factory {str(factory_status).lower()}")
    return "\n".join(lines)


def install() -> None:
    if getattr(_compact, "_truth_health_view_installed", False):
        return
    _compact._lane_text = lane_text
    _compact.strategy_room_text = strategy_room_text
    _compact._truth_health_view_installed = True


install()
