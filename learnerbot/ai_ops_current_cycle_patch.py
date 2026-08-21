from __future__ import annotations

from . import ai_ops_status as _status

_PREV_STRATEGY_STATUS = _status.strategy_status


def _matches_cycle(report: dict | None, status: dict) -> bool:
    if not report:
        return False
    cycle_id = str(status.get("cycle_id") or "")
    source = str(status.get("source_commit") or "")
    evidence = str(status.get("evidence_sha256") or "")
    if cycle_id and str(report.get("cycle_id") or "") != cycle_id:
        return False
    if source and report.get("source_commit") and str(report.get("source_commit")) != source:
        return False
    if evidence and report.get("evidence_sha256") and str(report.get("evidence_sha256")) != evidence:
        return False
    return bool(cycle_id)


def strategy_status_current_cycle(repo_root):
    status = _status.read_json(repo_root, "strategy/latest_status.json")
    if not status:
        return _PREV_STRATEGY_STATUS(repo_root)

    out = dict(status)
    out.setdefault("phase", "STRATEGY")
    cycle_id = str(out.get("cycle_id") or "")

    # Derive Claude directly from this immutable cycle.  This avoids a race where
    # latest_status.json is rewritten by the original three-agent publisher after
    # Claude has already published claude.json.
    claude = None
    if cycle_id:
        claude = _status.read_json(repo_root, f"strategy/runs/{cycle_id}/claude.json")
    out["claude"] = _status._agent_status(claude)

    # A global latest_master_decision.json may belong to an older Strategy cycle.
    # Prefer the cycle-local decision and only accept the global alias when its
    # immutable cycle/source/evidence identity matches the currently displayed run.
    master = None
    if cycle_id:
        candidate = _status.read_json(repo_root, f"strategy/runs/{cycle_id}/master_decision.json")
        if _matches_cycle(candidate, out):
            master = candidate
    if master is None:
        candidate = _status.read_json(repo_root, "strategy/latest_master_decision.json")
        if _matches_cycle(candidate, out):
            master = candidate

    out["master_decision_available"] = bool(master)
    out["decision_counts"] = _status.decision_counts(master)
    if master:
        out["master_status"] = str(master.get("status") or "AVAILABLE")
        out["master_cycle_id"] = str(master.get("cycle_id") or "")
    else:
        out["master_status"] = "WAITING"
        out["master_cycle_id"] = ""
    return out


def install():
    if getattr(_status, "_current_cycle_strategy_status_installed", False):
        return
    _status.strategy_status = strategy_status_current_cycle
    _status._current_cycle_strategy_status_installed = True


install()
