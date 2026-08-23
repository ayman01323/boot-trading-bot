from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 4
LOGICAL_AGENTS = (
    "gpt",
    "claude-general",
    "claude-coding",
    "gemini",
    "deepseek",
    "grok",
    "kimi",
    "copilot",
)
ALIASES = {
    "claude": "claude-general",
    "claude_general": "claude-general",
    "claude-coding": "claude-coding",
    "claude_coding": "claude-coding",
}
ATCS_MAX = {
    "correctness": 20.0,
    "evidence": 15.0,
    "marginal_value": 10.0,
    "actionability": 10.0,
    "collaboration": 5.0,
    "cost_efficiency": 5.0,
    "timeliness": 5.0,
}
ECONOMIC_MAX = {
    "net_edge": 15.0,
    "loss_prevention": 8.0,
    "target_quality": 7.0,
}
VALUE_WEIGHTS = {
    "atcs_90d": 0.40,
    "marginal_value_added": 0.20,
    "critical_specialization": 0.15,
    "independence_uniqueness": 0.10,
    "cost_efficiency": 0.10,
    "availability_reliability": 0.05,
}


def normalise_agent(value: str) -> str:
    name = str(value or "").strip().lower().replace(" ", "-")
    name = ALIASES.get(name, name)
    if name not in LOGICAL_AGENTS:
        raise ValueError(f"unsupported AI score identity: {name}")
    return name


def _root(base: Any) -> Path:
    if hasattr(base, "data_dir"):
        root = Path(getattr(base, "data_dir"))
    else:
        root = Path(base)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(base: Any) -> Path:
    return _root(base) / "ai_agent_target_score_v4.json"


def _default() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "contributions": [],
        "audits": [],
        "value_assessments": [],
    }


def _load(base: Any) -> dict[str, Any]:
    path = _path(base)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.setdefault("schema_version", SCHEMA_VERSION)
            value.setdefault("contributions", [])
            value.setdefault("audits", [])
            value.setdefault("value_assessments", [])
            return value
    except Exception:
        pass
    return _default()


def _save(base: Any, value: dict[str, Any]) -> None:
    path = _path(base)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _num(value: Any, *, low: float = 0.0, high: float = 100.0) -> float:
    number = float(value or 0.0)
    if number < low or number > high:
        raise ValueError(f"score {number} outside {low}..{high}")
    return number


def _record_id(prefix: str, payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return f"{prefix}-" + hashlib.sha256(raw).hexdigest()[:18]


def register_pending(
    base: Any,
    *,
    agent: str,
    contribution_id: str,
    category: str,
    case_id: str = "",
    source_sha: str = "",
    role: str = "",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
    created_at: int | None = None,
) -> dict[str, Any]:
    """Register real work without inventing a quality score before evidence exists."""
    agent = normalise_agent(agent)
    cid = str(contribution_id or "").strip()
    if not cid:
        raise ValueError("contribution_id is required")
    data = _load(base)
    rows = data["contributions"]
    for row in reversed(rows):
        if row.get("contribution_id") == cid and row.get("agent") == agent:
            return dict(row)
    now = int(created_at or time.time())
    row = {
        "record_id": _record_id("pending", {"agent": agent, "contribution_id": cid, "created_at": now}),
        "contribution_id": cid,
        "agent": agent,
        "category": str(category or "general")[:80],
        "case_id": str(case_id or "")[:160],
        "source_sha": str(source_sha or "")[:80],
        "role": str(role or "")[:160],
        "evidence_refs": [str(x)[:500] for x in (evidence_refs or [])][:50],
        "created_at": now,
        "status": "PENDING_SCORE",
        "outcome_resolved": False,
    }
    rows.append(row)
    data["contributions"] = rows[-5000:]
    _save(base, data)
    return dict(row)


def record_score(
    base: Any,
    *,
    agent: str,
    contribution_id: str,
    scorer: str,
    category: str,
    scores: dict[str, Any],
    economic: dict[str, Any] | None = None,
    penalties: dict[str, Any] | None = None,
    outcome_resolved: bool = False,
    material: bool = False,
    unsafe_flag: bool = False,
    case_id: str = "",
    source_sha: str = "",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
    notes: str = "",
    created_at: int | None = None,
) -> dict[str, Any]:
    """Record one ATCS assessment using the focused V4 scorecard.

    Economic impact is capped at 10/30 before an outcome is measured. UNKNOWN
    economic impact should be represented by zeros plus outcome_resolved=False,
    not by fabricated profit attribution.
    """
    agent = normalise_agent(agent)
    scorer_name = str(scorer or "").strip().lower().replace(" ", "-")
    if not scorer_name:
        raise ValueError("scorer is required")
    if agent == "gpt" and scorer_name == "gpt":
        raise ValueError("GPT cannot score its own contribution")
    cid = str(contribution_id or "").strip()
    if not cid:
        raise ValueError("contribution_id is required")

    clean_scores: dict[str, float] = {}
    for key, maximum in ATCS_MAX.items():
        clean_scores[key] = _num((scores or {}).get(key, 0.0), high=maximum)

    econ = economic or {}
    clean_econ: dict[str, float] = {}
    for key, maximum in ECONOMIC_MAX.items():
        clean_econ[key] = _num(econ.get(key, 0.0), high=maximum)
    economic_total = sum(clean_econ.values())
    if not outcome_resolved:
        economic_total = min(10.0, economic_total)

    penalty_rows = {str(k): max(0.0, float(v or 0.0)) for k, v in (penalties or {}).items()}
    penalty_total = sum(penalty_rows.values())
    gross = economic_total + sum(clean_scores.values())
    atcs = max(0.0, min(100.0, gross - penalty_total))
    audit_required = bool(agent == "gpt" or atcs > 70.0 or atcs < 30.0 or material or unsafe_flag)
    now = int(created_at or time.time())
    row = {
        "record_id": _record_id("score", {"agent": agent, "contribution_id": cid, "now": now, "scorer": scorer_name}),
        "contribution_id": cid,
        "agent": agent,
        "scorer": scorer_name,
        "category": str(category or "general")[:80],
        "case_id": str(case_id or "")[:160],
        "source_sha": str(source_sha or "")[:80],
        "created_at": now,
        "status": "SCORED",
        "scores": clean_scores,
        "economic": clean_econ,
        "economic_total": round(economic_total, 3),
        "penalties": penalty_rows,
        "penalty_total": round(penalty_total, 3),
        "atcs": round(atcs, 3),
        "outcome_resolved": bool(outcome_resolved),
        "material": bool(material),
        "unsafe_flag": bool(unsafe_flag),
        "audit_required": audit_required,
        "audit_status": "PENDING" if audit_required else "NOT_REQUIRED",
        "evidence_refs": [str(x)[:500] for x in (evidence_refs or [])][:50],
        "notes": str(notes or "")[:3000],
    }
    data = _load(base)
    data["contributions"].append(row)
    data["contributions"] = data["contributions"][-5000:]
    _save(base, data)
    return dict(row)


def audit_score(
    base: Any,
    *,
    contribution_id: str,
    auditor: str,
    status: str = "PASS",
    audited_score: float | None = None,
    notes: str = "",
    created_at: int | None = None,
) -> dict[str, Any]:
    data = _load(base)
    cid = str(contribution_id or "").strip()
    scored = [r for r in data["contributions"] if r.get("contribution_id") == cid and r.get("status") == "SCORED"]
    if not scored:
        raise ValueError("no scored contribution found")
    source = scored[-1]
    auditor_name = str(auditor or "").strip().lower().replace(" ", "-")
    if not auditor_name:
        raise ValueError("auditor is required")
    if auditor_name == source.get("agent"):
        raise ValueError("originating agent cannot audit its own score")
    state = str(status or "PASS").strip().upper()
    if state not in {"PASS", "CORRECTED", "REJECTED"}:
        raise ValueError("audit status must be PASS/CORRECTED/REJECTED")
    corrected = None if audited_score is None else _num(audited_score, high=100.0)
    if state == "CORRECTED" and corrected is None:
        raise ValueError("CORRECTED audit requires audited_score")
    if state == "REJECTED":
        corrected = 0.0
    now = int(created_at or time.time())
    row = {
        "audit_id": _record_id("audit", {"cid": cid, "auditor": auditor_name, "now": now}),
        "contribution_id": cid,
        "source_record_id": source.get("record_id"),
        "agent": source.get("agent"),
        "auditor": auditor_name,
        "status": state,
        "audited_score": corrected,
        "created_at": now,
        "notes": str(notes or "")[:3000],
    }
    data["audits"].append(row)
    data["audits"] = data["audits"][-5000:]
    _save(base, data)
    return dict(row)


def _effective_score(row: dict[str, Any], audits: list[dict[str, Any]]) -> float | None:
    if row.get("status") != "SCORED" or row.get("atcs") is None:
        return None
    matching = [a for a in audits if a.get("contribution_id") == row.get("contribution_id")]
    if not matching:
        return float(row["atcs"])
    latest = matching[-1]
    if latest.get("audited_score") is not None:
        return float(latest["audited_score"])
    return float(row["atcs"])


def value_band(value: float | None) -> str:
    if value is None:
        return "COLLECTING"
    if value >= 80:
        return "CORE"
    if value >= 65:
        return "KEEP"
    if value >= 50:
        return "SPECIALIST / PROBATION"
    if value >= 35:
        return "REDUCE"
    return "REMOVE CANDIDATE"


def record_value_assessment(
    base: Any,
    *,
    agent: str,
    assessor: str,
    dimensions: dict[str, Any],
    evidence_window_days: int,
    material_outcomes: int,
    consecutive_weak_windows: int = 0,
    ablation_passed: bool = False,
    no_unique_critical_specialization: bool = False,
    independently_audited: bool = False,
    notes: str = "",
    created_at: int | None = None,
) -> dict[str, Any]:
    agent = normalise_agent(agent)
    assessor_name = str(assessor or "").strip().lower().replace(" ", "-")
    if not assessor_name or assessor_name == agent:
        raise ValueError("value assessment requires a non-originating assessor")
    data = _load(base)
    now = int(created_at or time.time())
    cutoff = now - 90 * 86400
    latest_by_id: dict[str, dict[str, Any]] = {}
    for row in data["contributions"]:
        if row.get("agent") != agent or int(row.get("created_at") or 0) < cutoff or row.get("status") != "SCORED":
            continue
        latest_by_id[str(row.get("contribution_id"))] = row
    effective = [s for r in latest_by_id.values() if (s := _effective_score(r, data["audits"])) is not None]
    atcs_90d = sum(effective) / len(effective) if effective else 0.0
    dims = {
        "marginal_value_added": _num(dimensions.get("marginal_value_added", 0.0)),
        "critical_specialization": _num(dimensions.get("critical_specialization", 0.0)),
        "independence_uniqueness": _num(dimensions.get("independence_uniqueness", 0.0)),
        "cost_efficiency": _num(dimensions.get("cost_efficiency", 0.0)),
        "availability_reliability": _num(dimensions.get("availability_reliability", 0.0)),
    }
    avs = (
        atcs_90d * VALUE_WEIGHTS["atcs_90d"]
        + dims["marginal_value_added"] * VALUE_WEIGHTS["marginal_value_added"]
        + dims["critical_specialization"] * VALUE_WEIGHTS["critical_specialization"]
        + dims["independence_uniqueness"] * VALUE_WEIGHTS["independence_uniqueness"]
        + dims["cost_efficiency"] * VALUE_WEIGHTS["cost_efficiency"]
        + dims["availability_reliability"] * VALUE_WEIGHTS["availability_reliability"]
    )
    enough_evidence = int(evidence_window_days) >= 90 or int(material_outcomes) >= 30
    removal_candidate_gate = bool(
        avs < 35.0
        and enough_evidence
        and int(consecutive_weak_windows) >= 2
        and ablation_passed
        and no_unique_critical_specialization
        and independently_audited
    )
    row = {
        "assessment_id": _record_id("value", {"agent": agent, "now": now, "assessor": assessor_name}),
        "agent": agent,
        "assessor": assessor_name,
        "created_at": now,
        "atcs_90d": round(atcs_90d, 3),
        "dimensions": dims,
        "avs": round(avs, 3),
        "band": value_band(avs),
        "evidence_window_days": int(evidence_window_days),
        "material_outcomes": int(material_outcomes),
        "consecutive_weak_windows": int(consecutive_weak_windows),
        "ablation_passed": bool(ablation_passed),
        "no_unique_critical_specialization": bool(no_unique_critical_specialization),
        "independently_audited": bool(independently_audited),
        "removal_candidate_gate": removal_candidate_gate,
        "automatic_removal_allowed": False,
        "notes": str(notes or "")[:3000],
    }
    data["value_assessments"].append(row)
    data["value_assessments"] = data["value_assessments"][-2000:]
    _save(base, data)
    return dict(row)


def summary(base: Any, *, now: int | None = None) -> dict[str, Any]:
    data = _load(base)
    current = int(now or time.time())
    cutoff = current - 90 * 86400
    latest_scored: dict[tuple[str, str], dict[str, Any]] = {}
    pending: dict[str, set[str]] = {agent: set() for agent in LOGICAL_AGENTS}
    for row in data["contributions"]:
        agent = row.get("agent")
        if agent not in LOGICAL_AGENTS or int(row.get("created_at") or 0) < cutoff:
            continue
        cid = str(row.get("contribution_id") or "")
        if row.get("status") == "PENDING_SCORE":
            pending[agent].add(cid)
        elif row.get("status") == "SCORED":
            latest_scored[(agent, cid)] = row
            pending[agent].discard(cid)

    latest_value: dict[str, dict[str, Any]] = {}
    for row in data["value_assessments"]:
        if row.get("agent") in LOGICAL_AGENTS:
            latest_value[row["agent"]] = row

    agents: dict[str, Any] = {}
    for agent in LOGICAL_AGENTS:
        rows = [row for (name, _), row in latest_scored.items() if name == agent]
        values = [v for row in rows if (v := _effective_score(row, data["audits"])) is not None]
        economics = [float(row.get("economic_total") or 0.0) for row in rows]
        outcomes = sum(1 for row in rows if row.get("outcome_resolved"))
        audit_pending = 0
        for row in rows:
            if not row.get("audit_required"):
                continue
            matches = [a for a in data["audits"] if a.get("contribution_id") == row.get("contribution_id")]
            if not matches:
                audit_pending += 1
        value = latest_value.get(agent)
        agents[agent] = {
            "atcs_90d": round(sum(values) / len(values), 2) if values else None,
            "economic_90d": round(sum(economics) / len(economics), 2) if economics else None,
            "scored_contributions": len(rows),
            "outcome_resolved": outcomes,
            "pending_score": len(pending[agent]),
            "audit_pending": audit_pending,
            "avs": float(value.get("avs")) if value else None,
            "band": str(value.get("band")) if value else "COLLECTING",
            "removal_candidate_gate": bool(value.get("removal_candidate_gate")) if value else False,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "window_days": 90,
        "agents": agents,
        "updated_at": current,
        "automatic_removal_allowed": False,
    }
