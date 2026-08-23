from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

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
_PROVIDER_UNVERIFIED_RED_SECONDS = 3 * 60 * 60
_REVIEW_STALE_SECONDS = 90 * 60
_CONNECTION_STATUS_PATH = Path("/var/tmp/boot/ai_agent_ws_connections.json")
_HARD_FAILURE_STATES = {
    "NOT_WORKING",
    "FAILED",
    "ERROR",
    "BLOCKED",
    "BLOCKED_AUTH",
    "INCOMPLETE",
}


def _fresh_preflight() -> dict:
    """Return provider preflight evidence with explicit freshness metadata."""
    value = _warning.read_json(_compact._repo_root(), "health/provider_api_preflight.json") or {}
    try:
        checked = int(value.get("checked_epoch") or 0)
    except Exception:
        checked = 0
    age = max(0, int(time.time()) - checked) if checked else None
    value["_truth_age_seconds"] = age
    value["_truth_stale"] = bool(age is None or age > _PREFLIGHT_MAX_AGE_SECONDS)
    return value


def _is_production_runtime_process() -> bool:
    """True only for the long-running learnerbot service command.

    Tests and short administrative commands can instantiate the broker and therefore
    create a connection-truth file with their own PID. Those processes must never
    make a pure dashboard renderer consume that file as if it were production.
    Tests that need runtime truth can still monkeypatch _runtime_connections.
    """
    return len(sys.argv) >= 2 and str(sys.argv[1]).strip().lower() == "run"


def _runtime_connections() -> dict:
    """Return live broker registration truth only for the production process.

    The broker writes its current registered recipient set whenever a worker
    connects or disconnects. A PID mismatch means the file belongs to an older
    learnerbot process and is ignored rather than producing stale green health.
    """
    if not _is_production_runtime_process():
        return {}
    try:
        value = json.loads(_CONNECTION_STATUS_PATH.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        if int(value.get("pid") or 0) != os.getpid():
            return {}
        connected = {
            str(agent).strip().lower()
            for agent in (value.get("connected_agents") or [])
            if str(agent).strip().lower() in set(_compact.PROVIDERS)
        }
        return {
            "available": True,
            "connected_agents": connected,
            "updated_epoch": int(value.get("updated_epoch") or 0),
        }
    except Exception:
        return {}


def _age_text(seconds) -> str:
    try:
        seconds = max(0, int(seconds))
    except Exception:
        return "unknown"
    if seconds < 60:
        return "<1m"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


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


def _mapped_provider_status(provider: str, preflight: dict) -> tuple[str, str]:
    key = _PREFLIGHT_KEYS.get(provider)
    live = (preflight or {}).get(key) or {}
    live_state = str(live.get("state") or "").upper()
    age = (preflight or {}).get("_truth_age_seconds")
    stale = bool((preflight or {}).get("_truth_stale", True))

    if not live_state:
        return "🟡", "API status unavailable"

    if stale:
        age_label = _age_text(age)
        too_old = age is None or int(age) > _PROVIDER_UNVERIFIED_RED_SECONDS
        icon = "🔴" if too_old else "🟡"
        prefix = "API unverified for" if too_old else "API not checked for"
        last = "last OK" if live_state == "WORKING" else "last problem"
        return icon, f"{prefix} {age_label} · {last}"

    if live_state == "WORKING":
        return "🟢", "API working"
    return "🔴", "API/provider problem"


def _unmapped_agent_status(provider: str, engineering: dict, strategy: dict) -> tuple[str, str]:
    eng_detail = ((engineering or {}).get("agents") or {}).get(provider) or {}
    strat_detail = ((strategy or {}).get("agents") or {}).get(provider) or {}
    states = [
        str((detail or {}).get("state") or "WAITING").upper()
        for detail in (eng_detail, strat_detail)
    ]

    if provider == "copilot":
        assignments = {
            _copilot_assignment_state("engineering", engineering),
            _copilot_assignment_state("strategy", strategy),
        }
        assignments.discard("")
        if any(state in {"BLOCKED_AUTH", "FAILED", "ERROR"} for state in assignments):
            return "🔴", "Assignment/auth problem"
        if "ASSIGNED" in assignments:
            return "🟢", "Assigned"
        if assignments:
            return "🟡", "Assignment pending"

    if all(state == "WORKING" for state in states):
        return "🟢", "Agent working"
    if any(state in _HARD_FAILURE_STATES or state.startswith("BLOCKED_") for state in states):
        if any(state == "WORKING" for state in states):
            return "🟡", "Agent state mixed"
        return "🔴", "Agent problem"
    if any(state == "WORKING" for state in states):
        return "🟡", "Agent partly verified"
    return "🟡", "Agent state pending"


def _provider_status(
    provider: str,
    engineering: dict,
    strategy: dict,
    preflight: dict,
    runtime: dict,
) -> tuple[str, str]:
    """Prefer actual registered worker state over stale review-cycle state."""
    if (runtime or {}).get("available"):
        connected = provider in ((runtime or {}).get("connected_agents") or set())
        if not connected:
            return "🔴", "Worker disconnected"

        key = _PREFLIGHT_KEYS.get(provider)
        if key:
            live = (preflight or {}).get(key) or {}
            live_state = str(live.get("state") or "").upper()
            stale = bool((preflight or {}).get("_truth_stale", True))
            age = (preflight or {}).get("_truth_age_seconds")
            if live_state and not stale and live_state != "WORKING":
                return "🔴", "Worker connected · API/provider problem"
            if live_state == "WORKING":
                if stale:
                    return "🟢", f"Worker connected · API last OK {_age_text(age)} ago"
                return "🟢", "Worker connected · API working"
            if live_state and stale:
                return "🟡", f"Worker connected · API last problem {_age_text(age)} ago"
        return "🟢", "Worker connected"

    if provider in _PREFLIGHT_KEYS:
        return _mapped_provider_status(provider, preflight)
    return _unmapped_agent_status(provider, engineering, strategy)


def provider_health_text(engineering: dict, strategy: dict) -> str:
    """Show live agent runtime once, with provider API evidence as secondary detail."""
    preflight = _fresh_preflight()
    runtime = _runtime_connections()
    rows: list[tuple[str, str, str]] = []
    for provider in _compact.PROVIDERS:
        icon, status = _provider_status(provider, engineering, strategy, preflight, runtime)
        rows.append((provider, icon, status))

    healthy = sum(1 for _provider, icon, _status in rows if icon == "🟢")
    verify = sum(1 for _provider, icon, _status in rows if icon == "🟡")
    problems = len(rows) - healthy - verify
    overall = "🔴" if problems else ("🟡" if verify else "🟢")

    lines = [
        _compact._AI_HEALTH_HEADING,
        f"{overall} <b>{healthy} healthy</b> · {verify} need verification · {problems} problems",
    ]
    for provider, icon, status in rows:
        lines.append(f"{icon} {_compact._LABELS[provider]} — {status}")
    return "\n".join(lines)


def _classified_rows(lane: str, health: dict) -> list[tuple[str, str, str]]:
    agents = (health or {}).get("agents") or {}
    rows: list[tuple[str, str, str]] = []
    for provider in _compact.PROVIDERS:
        detail = agents.get(provider) or {
            "state": "WAITING",
            "reason": f"{provider} report has not completed",
        }
        icon, status = _compact.classify_health(lane, provider, detail)
        rows.append((provider, icon, str(status)))
    return rows


def _summary_for_rows(rows: list[tuple[str, str, str]]) -> tuple[str, str]:
    working = sum(1 for _provider, icon, _status in rows if icon == "🟢")
    pending = sum(1 for _provider, icon, _status in rows if icon == "🟡")
    issues = len(rows) - working - pending
    icons = {icon for _provider, icon, _status in rows}
    if "🔴" in icons:
        overall = "🔴"
    elif "🟠" in icons:
        overall = "🟠"
    elif "🟡" in icons:
        overall = "🟡"
    else:
        overall = "🟢"
    return overall, f"{working} working · {pending} in progress · {issues} issues"


def _current_checkout_sha() -> str:
    root = _compact._repo_root()
    git_dir = root / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(":", 1)[1].strip()
        ref_path = git_dir / ref
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        packed = (git_dir / "packed-refs").read_text(encoding="utf-8")
        for line in packed.splitlines():
            if line.startswith("#") or line.startswith("^"):
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    except Exception:
        pass
    return ""


def _review_source_commit(lane: str, health: dict) -> str:
    source = str((health or {}).get("source_commit") or "").strip()
    if source:
        return source
    # Only a real collector snapshot may borrow persisted strategy metadata.
    # Explicit/synthetic health dictionaries must remain self-contained so a
    # caller cannot be marked stale because of unrelated files on disk.
    if lane == "strategy" and bool((health or {}).get("available")):
        latest = _warning.read_json(_compact._repo_root(), "strategy/latest_status.json") or {}
        return str(latest.get("source_commit") or "").strip()
    return ""


def _review_stale_reason(lane: str, health: dict) -> str:
    source = _review_source_commit(lane, health)
    current = _current_checkout_sha()
    if source and current and source != current:
        return "Review snapshot predates current code"
    try:
        age = int((health or {}).get("age_seconds"))
    except Exception:
        age = 0
    if age > _REVIEW_STALE_SECONDS:
        return f"Review snapshot {_age_text(age)} old"
    return ""


def lane_summary_text(lane: str, health: dict) -> str:
    heading = _compact._ENGINEERING_HEADING if lane == "engineering" else _compact._STRATEGY_HEADING
    stale = _review_stale_reason(lane, health)
    if stale:
        return "\n".join([heading, f"🟡 <b>{stale} · refresh needed</b>"])

    rows = _classified_rows(lane, health)
    overall, summary = _summary_for_rows(rows)
    lines = [heading, f"{overall} <b>{summary}</b>"]
    for provider, icon, status in rows:
        if icon in {"🔴", "🟠"}:
            lines.append(f"{icon} {_compact._LABELS[provider]} — {status}")
    return "\n".join(lines)


def factory_summary_text(health: dict) -> str:
    agents = (health or {}).get("agents") or {}
    if agents and all(
        str((agents.get(provider) or {}).get("state") or "WAITING").upper() == "WAITING"
        and any(
            marker in str((agents.get(provider) or {}).get("reason") or "").lower()
            for marker in ("no strategy room request", "stale")
        )
        for provider in _compact.PROVIDERS
    ):
        return "\n".join([
            _compact._STRATEGY_FACTORY_HEADING,
            "⚪ <b>Idle · no active factory request</b>",
        ])

    rows = _classified_rows("strategy_room", health)
    overall, summary = _summary_for_rows(rows)
    lines = [_compact._STRATEGY_FACTORY_HEADING, f"{overall} <b>{summary}</b>"]
    for provider, icon, status in rows:
        if icon in {"🔴", "🟠"}:
            lines.append(f"{icon} {_compact._LABELS[provider]} — {status}")
    return "\n".join(lines)


def lane_text(lane: str, health: dict | None = None) -> str:
    """Dedicated drill-down: show review state only, never duplicate API health."""
    health = health if health is not None else _compact._lane_health(lane)
    heading = _compact._ENGINEERING_HEADING if lane == "engineering" else _compact._STRATEGY_HEADING
    rows = _classified_rows(lane, health)
    lines = [heading]
    stale = _review_stale_reason(lane, health)
    if stale:
        lines.append(f"🟡 {stale} · historical detail below")
    for provider, icon, status in rows:
        lines.append(f"{icon} {_compact._LABELS[provider]} — {status}")
    return "\n".join(lines)


def strategy_room_text(health: dict | None = None) -> str:
    """Dedicated drill-down: show factory work state only."""
    health = health if health is not None else _compact._strategy_room_health()
    rows = _classified_rows("strategy_room", health)
    lines = [_compact._STRATEGY_FACTORY_HEADING]
    for provider, icon, status in rows:
        lines.append(f"{icon} {_compact._LABELS[provider]} — {status}")
    return "\n".join(lines)


def dashboard_text(
    engineering: dict | None = None,
    strategy: dict | None = None,
    strategy_room: dict | None = None,
) -> str:
    """MASTER view: live runtime once, then compact operational summaries."""
    engineering = engineering if engineering is not None else _compact._lane_health("engineering")
    strategy = strategy if strategy is not None else _compact._lane_health("strategy")
    strategy_room = strategy_room if strategy_room is not None else _compact._strategy_room_health()
    return "\n\n".join(
        [
            provider_health_text(engineering, strategy),
            lane_summary_text("engineering", engineering),
            lane_summary_text("strategy", strategy),
            factory_summary_text(strategy_room),
        ]
    )


def install() -> None:
    if getattr(_compact, "_truth_health_view_installed", False):
        return
    _compact._lane_text = lane_text
    _compact.strategy_room_text = strategy_room_text
    _compact.dashboard_text = dashboard_text
    _compact._truth_health_view_installed = True


install()
