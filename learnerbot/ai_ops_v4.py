from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 4
SEVERITIES = ("P0", "P1", "P2", "P3")
MONITORS = {"STRATEGY", "ENGINEERING", "BOTH", "FACTORY", "AI_HEALTH"}
PROTECTED_ACTIONS = {
    "LIVE", "ARMED", "CAPITAL", "WALLET", "SIGNING", "DEPLOY", "PROMOTE_LIVE",
    "CHANGE_RISK", "STOP_LOSS", "CIRCUIT_BREAKER", "SEND_FUNDS",
}
SAFE_FACTORY_ACTIONS = ("REPORT", "RESEARCH", "SHADOW_PROPOSE", "CODE_DRAFT")
SCORE_MAX = {
    "evidence": 15,
    "correctness": 15,
    "novelty": 10,
    "actionability": 10,
    "expected_benefit": 10,
    "timeliness": 5,
    "clarity": 5,
    "realised_impact": 20,
    "cost_efficiency": 5,
    "durability": 5,
}
SCORE_PENALTIES = {
    "unsupported_evidence": 25,
    "duplicate": 8,
    "false_positive": 12,
    "unsafe_suggestion": 40,
    "hidden_uncertainty": 10,
    "overclaiming": 10,
}


def _ops_root(base: Any) -> Path:
    if hasattr(base, "data_dir"):
        root = Path(getattr(base, "data_dir"))
    else:
        root = Path(base)
    path = root / "ai_ops_v4"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return default


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _now() -> int:
    return int(time.time())


def _stable_hash(value: Any, n: int = 20) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:n]


def _normalise_severity(value: str | None, *, default: str = "P3") -> str:
    sev = str(value or default).strip().upper()
    return sev if sev in SEVERITIES else default


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    low = str(text or "").casefold()
    return any(word in low for word in words)


def classify_owner(event_type: str, message: str = "", explicit: str = "") -> str:
    if explicit:
        owner = str(explicit).upper().strip()
        if owner not in MONITORS:
            raise ValueError(f"unsupported owner monitor: {owner}")
        return owner
    et = str(event_type or "").upper().strip()
    if et == "LIVE_LOSS_ALERT":
        technical = _contains_any(message, (
            "latency", "rpc", "execution", "sellability", "reconciliation", "quote", "simulation",
            "landed", "slippage", "timeout", "nonce", "route", "liquidity failure",
        ))
        return "BOTH" if technical else "STRATEGY"
    if et == "ENGINEERING_REPORT":
        return "ENGINEERING"
    if et == "STRATEGY_REPORT":
        return "STRATEGY"
    if et == "FACTORY_REPORT":
        return "FACTORY"
    if et == "AI_HEALTH_WARNING":
        return "AI_HEALTH"
    if et == "WARNING":
        strategy = _contains_any(message, (
            "p&l", "profit", "loss", "drawdown", "strategy", "sellability", "liquidity", "position",
            "entry", "exit", "slippage", "opportunity",
        ))
        engineering = _contains_any(message, (
            "rpc", "latency", "execution", "disk", "memory", "cpu", "bandwidth", "api", "timeout",
            "reconciliation", "workflow", "server", "network", "database",
        ))
        if strategy and engineering:
            return "BOTH"
        if strategy:
            return "STRATEGY"
        if engineering:
            return "ENGINEERING"
    return "FACTORY"


def telegram_mode_for(severity: str) -> str:
    sev = _normalise_severity(severity)
    if sev in {"P0", "P1"}:
        return "IMMEDIATE"
    if sev == "P2":
        return "GROUPED"
    return "DIGEST"


def _event_key(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": payload.get("event_type"),
        "source_component": payload.get("source_component"),
        "chain": payload.get("chain"),
        "strategy_id": payload.get("strategy_id"),
        "strategy_version": payload.get("strategy_version"),
        "git_sha": payload.get("git_sha"),
        "trade_ids": sorted(str(x) for x in (payload.get("trade_ids") or [])),
        "message": " ".join(str(payload.get("message") or "").split())[:1200],
    }


def _needs_case(event: dict[str, Any]) -> bool:
    return bool(
        event.get("event_type") == "LIVE_LOSS_ALERT"
        or event.get("severity") in {"P0", "P1"}
        or (event.get("severity") == "P2" and int(event.get("occurrence_count") or 1) >= 3)
    )


def _case_allowed_actions(owner: str) -> list[str]:
    actions = ["REPORT", "RESEARCH", "SHADOW_PROPOSE"]
    if owner in {"ENGINEERING", "BOTH"}:
        actions.append("CODE_DRAFT")
    return actions


def _upsert_case(root: Path, event: dict[str, Any]) -> dict[str, Any] | None:
    if not _needs_case(event):
        return None
    path = root / "cases.json"
    cases = _read_json(path, [])
    if not isinstance(cases, list):
        cases = []
    correlation_id = str(event.get("correlation_id") or event.get("event_id"))
    case = next((row for row in cases if str((row or {}).get("correlation_id")) == correlation_id), None)
    now = _now()
    owner = str(event.get("owner_monitor") or "FACTORY")
    if case is None:
        case_id = "case-" + _stable_hash({"correlation_id": correlation_id, "event": event.get("event_type")}, 16)
        case = {
            "schema_version": SCHEMA_VERSION,
            "case_id": case_id,
            "correlation_id": correlation_id,
            "created_at": now,
            "updated_at": now,
            "severity": event.get("severity"),
            "owner_monitor": owner,
            "case_status": "OPEN",
            "event_ids": [],
            "event_type": event.get("event_type"),
            "evidence_refs": [],
            "root_cause_questions": [
                "What is directly proven by evidence?",
                "What alternative explanation could falsify the current hypothesis?",
                "Is this strategy deterioration, an engineering/execution confound, or both?",
                "Which monitor should have caught this earlier?",
            ],
            "required_challenger": True,
            "factory_case_id": "factory-" + _stable_hash(correlation_id, 14),
            "allowed_actions": _case_allowed_actions(owner),
            "protected_actions_denied": sorted(PROTECTED_ACTIONS),
            "resolution": "",
        }
        cases.append(case)
    case["updated_at"] = now
    case["severity"] = min((str(case.get("severity") or "P3"), str(event.get("severity") or "P3")), key=lambda s: SEVERITIES.index(s) if s in SEVERITIES else 3)
    if owner != case.get("owner_monitor"):
        case["owner_monitor"] = "BOTH" if {owner, case.get("owner_monitor")} & {"STRATEGY", "ENGINEERING", "BOTH"} else owner
    event_id = str(event.get("event_id") or "")
    if event_id and event_id not in case["event_ids"]:
        case["event_ids"].append(event_id)
    for ref in event.get("evidence_refs") or []:
        value = str(ref)
        if value and value not in case["evidence_refs"]:
            case["evidence_refs"].append(value)
    _atomic_json(path, cases[-500:])
    return dict(case)


def record_event(
    base: Any,
    *,
    event_type: str,
    source_component: str,
    message: str,
    severity: str = "P3",
    chain: str = "",
    strategy_id: str = "",
    strategy_version: str = "",
    git_sha: str = "",
    trade_ids: list[str] | tuple[str, ...] | None = None,
    financial_impact: Any = None,
    technical_impact: str = "",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
    correlation_id: str = "",
    owner_monitor: str = "",
    allowed_actions: list[str] | tuple[str, ...] | None = None,
    resolution: str = "",
) -> dict[str, Any]:
    root = _ops_root(base)
    path = root / "events.json"
    events = _read_json(path, [])
    if not isinstance(events, list):
        events = []
    now = _now()
    et = str(event_type or "WARNING").strip().upper()
    sev = _normalise_severity(severity)
    owner = classify_owner(et, message, owner_monitor)
    requested_actions = [str(x).upper().strip() for x in (allowed_actions or []) if str(x).strip()]
    if any(action in PROTECTED_ACTIONS for action in requested_actions):
        raise ValueError("AI Ops event cannot authorise protected actions")
    payload = {
        "event_type": et,
        "source_component": str(source_component or "unknown")[:160],
        "chain": str(chain or "")[:80],
        "strategy_id": str(strategy_id or "")[:160],
        "strategy_version": str(strategy_version or "")[:160],
        "git_sha": str(git_sha or "")[:80],
        "trade_ids": [str(x)[:160] for x in (trade_ids or [])][:50],
        "message": str(message or "")[:4000],
    }
    fingerprint = _stable_hash(_event_key(payload), 24)
    existing = next((row for row in reversed(events) if str((row or {}).get("dedup_fingerprint")) == fingerprint), None)
    if existing is not None:
        existing["last_seen_at"] = now
        existing["occurrence_count"] = int(existing.get("occurrence_count") or 1) + 1
        if SEVERITIES.index(sev) < SEVERITIES.index(str(existing.get("severity") or "P3")):
            existing["severity"] = sev
            existing["telegram_mode"] = telegram_mode_for(sev)
        event = existing
    else:
        event_id = "evt-" + _stable_hash({"fingerprint": fingerprint, "created_at": now}, 18)
        corr = str(correlation_id or "corr-" + _stable_hash(_event_key(payload), 18))
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "event_type": et,
            "source_component": payload["source_component"],
            "created_at": now,
            "last_seen_at": now,
            "severity": sev,
            "chain": payload["chain"],
            "strategy_id": payload["strategy_id"],
            "strategy_version": payload["strategy_version"],
            "git_sha": payload["git_sha"],
            "trade_ids": payload["trade_ids"],
            "financial_impact": financial_impact,
            "technical_impact": str(technical_impact or "")[:1200],
            "message": payload["message"],
            "evidence_refs": [str(x)[:500] for x in (evidence_refs or [])][:50],
            "dedup_fingerprint": fingerprint,
            "correlation_id": corr,
            "owner_monitor": owner,
            "telegram_mode": telegram_mode_for(sev),
            "case_status": "OPEN" if sev in {"P0", "P1"} or et == "LIVE_LOSS_ALERT" else "RECORDED",
            "factory_case_id": "",
            "allowed_actions": requested_actions or ["REPORT"],
            "protected_actions_denied": sorted(PROTECTED_ACTIONS),
            "resolution": str(resolution or "")[:2000],
            "occurrence_count": 1,
        }
        events.append(event)
    case = _upsert_case(root, event)
    if case:
        event["factory_case_id"] = case.get("factory_case_id", "")
        event["case_status"] = case.get("case_status", "OPEN")
    _atomic_json(path, events[-1000:])
    return {"event": dict(event), "case": case}


def list_events(base: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    events = _read_json(_ops_root(base) / "events.json", [])
    return list(events[-max(1, int(limit)):]) if isinstance(events, list) else []


def list_cases(base: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    cases = _read_json(_ops_root(base) / "cases.json", [])
    return list(cases[-max(1, int(limit)):]) if isinstance(cases, list) else []


def record_gap_report(base: Any, report: dict[str, Any]) -> dict[str, Any]:
    root = _ops_root(base)
    path = root / "implementation_gaps.json"
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        rows = []
    required = ("proposal", "why_blocked", "cheapest_safe_option", "expected_benefit", "validation_plan", "rollback", "decision")
    missing = [key for key in required if not str(report.get(key) or "").strip()]
    if missing:
        raise ValueError("implementation gap missing required fields: " + ", ".join(missing))
    decision = str(report.get("decision") or "").upper().strip()
    if decision not in {"BUILD", "BUY", "DEFER", "REJECT"}:
        raise ValueError("implementation gap decision must be BUILD/BUY/DEFER/REJECT")
    now = _now()
    row = dict(report)
    row.update({
        "schema_version": SCHEMA_VERSION,
        "gap_id": str(row.get("gap_id") or "gap-" + _stable_hash({"proposal": row.get("proposal"), "now": now}, 16)),
        "created_at": int(row.get("created_at") or now),
        "decision": decision,
    })
    rows.append(row)
    _atomic_json(path, rows[-500:])
    return row


def list_gap_reports(base: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = _read_json(_ops_root(base) / "implementation_gaps.json", [])
    return list(rows[-max(1, int(limit)):]) if isinstance(rows, list) else []


def record_score(
    base: Any,
    *,
    agent: str,
    scorer: str,
    contribution_id: str,
    category: str,
    dimensions: dict[str, Any],
    penalties: list[str] | tuple[str, ...] | None = None,
    material_live_or_governance: bool = False,
    notes: str = "",
) -> dict[str, Any]:
    agent = str(agent or "").strip().lower()
    scorer = str(scorer or "").strip().lower()
    if not agent or not scorer:
        raise ValueError("agent and scorer are required")
    if agent == scorer:
        raise ValueError("an agent cannot score its own contribution")
    raw_total = 0
    clean: dict[str, int] = {}
    for key, maximum in SCORE_MAX.items():
        try:
            value = int(dimensions.get(key, 0))
        except Exception:
            value = 0
        value = max(0, min(maximum, value))
        clean[key] = value
        raw_total += value
    applied_penalties: list[str] = []
    penalty_points = 0
    for name in penalties or []:
        key = str(name or "").strip().lower()
        if key in SCORE_PENALTIES and key not in applied_penalties:
            applied_penalties.append(key)
            penalty_points += SCORE_PENALTIES[key]
    score = max(0, min(100, raw_total - penalty_points))
    audit_required = bool(score >= 90 or score <= 30 or material_live_or_governance)
    now = _now()
    row = {
        "schema_version": SCHEMA_VERSION,
        "score_id": "score-" + _stable_hash({"agent": agent, "contribution_id": contribution_id, "now": now}, 18),
        "contribution_id": str(contribution_id or "")[:180],
        "agent": agent,
        "scorer": scorer,
        "category": str(category or "GENERAL")[:80],
        "dimensions": clean,
        "raw_total": raw_total,
        "penalties": applied_penalties,
        "penalty_points": penalty_points,
        "score": score,
        "status": "PROVISIONAL",
        "audit_required": audit_required,
        "audit_status": "PENDING" if audit_required else "NOT_REQUIRED",
        "material_live_or_governance": bool(material_live_or_governance),
        "notes": str(notes or "")[:1200],
        "created_at": now,
        "outcome_review_due_7d": now + 7 * 86400,
        "outcome_review_due_30d": now + 30 * 86400,
    }
    path = _ops_root(base) / "scores.json"
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        rows = []
    rows.append(row)
    _atomic_json(path, rows[-1000:])
    return row


def audit_score(base: Any, *, score_id: str, auditor: str, accepted: bool, reason: str = "") -> dict[str, Any]:
    path = _ops_root(base) / "scores.json"
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        raise ValueError("score ledger unavailable")
    target = next((row for row in rows if str((row or {}).get("score_id")) == str(score_id)), None)
    if target is None:
        raise ValueError("unknown score_id")
    auditor = str(auditor or "").strip().lower()
    if not auditor or auditor in {str(target.get("agent") or "").lower(), str(target.get("scorer") or "").lower()}:
        raise ValueError("score auditor must be independent of agent and scorer")
    target["audit_status"] = "ACCEPTED" if accepted else "REJECTED"
    target["auditor"] = auditor
    target["audit_reason"] = str(reason or "")[:1200]
    target["audited_at"] = _now()
    _atomic_json(path, rows[-1000:])
    return dict(target)


def list_scores(base: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = _read_json(_ops_root(base) / "scores.json", [])
    return list(rows[-max(1, int(limit)):]) if isinstance(rows, list) else []


def score_auditor_for_week(epoch: int | None = None) -> str:
    epoch = int(epoch or _now())
    week = epoch // (7 * 86400)
    auditors = ("claude-general", "gemini", "deepseek", "grok", "copilot", "claude-coding")
    return auditors[week % len(auditors)]


def engineering_rotation_for_day(epoch: int | None = None) -> dict[str, Any]:
    epoch = int(epoch or _now())
    tm = time.gmtime(epoch)
    # Python Monday=0..Sunday=6 after conversion from tm_wday.
    weekday = int(tm.tm_wday)
    agents = ["gpt", "claude-general", "gemini", "deepseek", "grok", "copilot"]
    week_index = epoch // (7 * 86400)
    shift = week_index % len(agents)
    rotated = agents[shift:] + agents[:shift]
    if weekday == 6:
        assigned: str | list[str] = list(agents)
        mode = "JOINT_ALL_SIX"
    else:
        assigned = rotated[weekday]
        mode = "SINGLE_PRIMARY"
    return {
        "schema_version": SCHEMA_VERSION,
        "epoch": epoch,
        "weekday": weekday,
        "week_shift": shift,
        "mode": mode,
        "assigned": assigned,
        "exploratory_review_required": True,
        "author_conflict_rule": "originating author cannot be sole reviewer of its own material change",
    }
