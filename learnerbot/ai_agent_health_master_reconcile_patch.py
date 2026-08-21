from __future__ import annotations

import json
import time
from pathlib import Path

from . import ai_agent_health_warning_patch as _health
from .ai_ops_status import read_json

_PREV_STRATEGY_HEALTH = _health._strategy_health


def _read_json(root: Path, path: str) -> dict | None:
    """Read a local test/runtime fixture when present, otherwise read ai-reviews.

    Production strategy artifacts normally live on the fetched ``ai-reviews``
    remote ref and ``ai_ops_status.read_json`` is authoritative for that case.
    Unit/deployment tests intentionally construct an isolated filesystem tree;
    reading that tree first makes the reconciler deterministic instead of
    accidentally depending on whatever remote ai-reviews cycle happens to be
    cached in the checkout running pytest.
    """
    local = Path(root) / path
    if local.is_file():
        try:
            value = json.loads(local.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except Exception:
            return None
    return read_json(root, path)


def _valid_report(report: dict | None, *, provider: str, cycle: str, source: str, evidence: str) -> bool:
    if not isinstance(report, dict):
        return False
    return bool(
        str(report.get("provider") or "").lower() == provider
        and str(report.get("cycle_id") or "") == cycle
        and str(report.get("source_commit") or "") == source
        and str(report.get("evidence_sha256") or "") == evidence
        and str(report.get("status") or "").upper() not in {"", "INCOMPLETE"}
    )


def _strategy_health_reconciled(root: Path, now: int) -> dict:
    # For isolated fixtures, the base health function must see the same local
    # files as this reconciliation layer.  Temporarily route only its module-level
    # read_json binding through the deterministic reader, then restore it.
    local_status = Path(root) / "strategy/latest_status.json"
    if local_status.is_file():
        original = _health.read_json
        _health.read_json = _read_json
        try:
            part = _PREV_STRATEGY_HEALTH(root, now)
        finally:
            _health.read_json = original
    else:
        part = _PREV_STRATEGY_HEALTH(root, now)

    if not part.get("available"):
        return part

    cycle = str(part.get("cycle") or "")
    if not cycle:
        return part
    run = f"strategy/runs/{cycle}"
    context = _read_json(root, f"{run}/context.json") or {}
    source = str(context.get("source_commit") or "")
    evidence = str(context.get("evidence_sha256") or "")

    reports = {
        name: _read_json(root, f"{run}/{name}.json")
        for name in ("gpt", "gemini", "copilot")
    }
    valid = {
        name
        for name, report in reports.items()
        if _valid_report(report, provider=name, cycle=cycle, source=source, evidence=evidence)
    }

    # The primary hourly workflow may still say BLOCKED_AUTH/WAITING after the
    # resilient Master has already adjudicated a partial cycle. The per-cycle
    # Master artifact is authoritative for whether reconciliation completed.
    master = _read_json(root, f"{run}/master_decision.json") or {}
    master_decided = bool(
        str(master.get("cycle_id") or "") == cycle
        and str(master.get("source_commit") or "") == source
        and str(master.get("evidence_sha256") or "") == evidence
        and str(master.get("status") or "")
    )
    completion = _read_json(root, f"{run}/completion.json") or {}
    if master_decided or bool(completion.get("master_decision_available")):
        part["master"] = "DECIDED" if len(valid) == 3 else "DECIDED_PARTIAL"
        part["valid_count"] = len(valid)

    # Prefer the reconciler's latest assignment evidence over stale top-level
    # BLOCKED_AUTH status. ASSIGNED means Copilot itself is reachable for task
    # assignment; it may still need time to produce the report.
    assignment = _read_json(root, f"{run}/copilot_assignment_reconciled.json") or {}
    assignment_state = str(assignment.get("assignment_state") or "").upper()
    assignment_reason = _health._clean_reason(
        assignment.get("reason") or assignment_state or "Copilot report has not completed"
    )
    if "copilot" not in valid and assignment_state:
        if assignment_state == "ASSIGNED":
            checked = int(assignment.get("checked_epoch") or 0)
            age = max(0, int(now) - checked) if checked else 10**9
            if age < _health.WAIT_GRACE_SECONDS:
                part["agents"]["copilot"] = {
                    "state": "WAITING",
                    "reason": "Copilot is assigned; waiting for its report",
                }
            else:
                part["agents"]["copilot"] = {
                    "state": "NOT_WORKING",
                    "reason": "Copilot is assigned but has not delivered a valid report within 30 minutes",
                }
        elif assignment_state in {"AWAITING_ASSIGNMENT", "WAITING_ASSIGNMENT"}:
            part["agents"]["copilot"] = {
                "state": "WAITING",
                "reason": assignment_reason,
            }
        elif assignment_state == "BLOCKED_AUTH":
            part["agents"]["copilot"] = {
                "state": "NOT_WORKING",
                "reason": assignment_reason,
            }

    # Recompute from the final reconciled states, while retaining an already
    # completed partial Master decision even when one or two agents are unhealthy.
    part["valid_count"] = sum(
        1 for row in (part.get("agents") or {}).values() if row.get("state") == "WORKING"
    )
    if master_decided:
        part["master"] = "DECIDED" if part["valid_count"] == 3 else "DECIDED_PARTIAL"
    return part


def install() -> None:
    _health._strategy_health = _strategy_health_reconciled


install()
