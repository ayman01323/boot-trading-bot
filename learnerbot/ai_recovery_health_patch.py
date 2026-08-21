from __future__ import annotations

from . import ai_four_agent_health_patch as _health5

_PREV_ENGINEERING_HEALTH = _health5._engineering_health
_PREV_STRATEGY_HEALTH = _health5._strategy_health


def _engineering_health(root, now: int) -> dict:
    out = dict(_PREV_ENGINEERING_HEALTH(root, now) or {})
    source = str(out.get("source_commit") or "")
    agents = dict(out.get("agents") or {})
    if source and "copilot" in agents and agents["copilot"].get("state") != "WORKING":
        assignment = _health5._health.read_json(
            root, f"weekly/runs/{source}/copilot_assignment_reconciled.json"
        ) or {}
        if str(assignment.get("assignment_state") or "").upper() == "ASSIGNED":
            agents["copilot"] = {
                "state": "WAITING",
                "reason": str(assignment.get("reason") or "Copilot is assigned; report is in progress."),
            }
    out["agents"] = agents
    out["valid_count"] = sum(1 for row in agents.values() if row.get("state") == "WORKING")
    return out


def _strategy_health(root, now: int) -> dict:
    out = dict(_PREV_STRATEGY_HEALTH(root, now) or {})
    agents = dict(out.get("agents") or {})
    status = _health5._health.read_json(root, "strategy/latest_status.json") or {}
    copilot_state = str(status.get("copilot") or "").upper()
    if "copilot" in agents and agents["copilot"].get("state") != "WORKING" and copilot_state in {
        "WAITING", "ASSIGNED", "WAITING_FOR_REPORT", "WAITING_ASSIGNMENT"
    }:
        agents["copilot"] = {
            "state": "WAITING",
            "reason": str(status.get("copilot_assignment_reason") or "Copilot is assigned; report is in progress."),
        }
    out["agents"] = agents
    out["valid_count"] = sum(1 for row in agents.values() if row.get("state") == "WORKING")
    return out


def install() -> None:
    _health5._engineering_health = _engineering_health
    _health5._strategy_health = _strategy_health
    _health5._health._engineering_health = _engineering_health
    _health5._health._strategy_health = _strategy_health


install()
