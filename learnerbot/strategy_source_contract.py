from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROVIDERS = {"gpt", "gemini", "copilot"}
AGENT_STATUSES = {"HEALTHY", "CHANGES_PROPOSED", "INCOMPLETE"}
DISPOSITIONS = {"ACCEPT", "REJECT", "DEFER"}
SOURCE_CLASSES = {
    "PRIMARY_RAW_DATA",
    "OFFICIAL_API_WEBSOCKET",
    "OPEN_SOURCE_DATA_LIBRARY",
    "OPEN_SOURCE_BACKTEST_FRAMEWORK",
    "OPEN_SOURCE_EXECUTION_FRAMEWORK",
    "ONCHAIN_INFRASTRUCTURE",
    "ACADEMIC_RESEARCH",
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _https(value: Any) -> bool:
    try:
        p = urlparse(str(value or "").strip())
        return p.scheme == "https" and bool(p.netloc)
    except Exception:
        return False


def _validate_source_row(row: dict, *, prefix: str, enforce_operational_flags: bool = True) -> None:
    if not isinstance(row, dict):
        raise ValueError(f"{prefix} must be an object")
    for key in ("id", "name", "source_class", "official_url", "publisher", "intended_use", "trust_basis"):
        if not str(row.get(key) or "").strip():
            raise ValueError(f"{prefix} missing {key}")
    if str(row.get("source_class") or "") not in SOURCE_CLASSES:
        raise ValueError(f"{prefix} has invalid source_class")
    if not _https(row.get("official_url")):
        raise ValueError(f"{prefix} official_url must be https")
    chains = row.get("chain_scope")
    if not isinstance(chains, list) or not chains:
        raise ValueError(f"{prefix} needs chain_scope")
    if any(str(x).upper() not in {"SOLANA", "EVM", "CEX", "GENERAL"} for x in chains):
        raise ValueError(f"{prefix} has unsupported chain scope")

    # Agent reports are an input contract and therefore fail validation when they
    # request operational use.  GPT Master output is different: it is passed through
    # a deterministic policy gate, so unsafe boolean values must remain parseable in
    # order for the gate to mark them explicitly ineligible instead of crashing the
    # whole reconciliation cycle.
    if not isinstance(row.get("research_only"), bool):
        raise ValueError(f"{prefix} research_only must be boolean")
    if not isinstance(row.get("automatic_execution_allowed"), bool):
        raise ValueError(f"{prefix} automatic_execution_allowed must be boolean")
    if enforce_operational_flags:
        if row.get("research_only") is not True:
            raise ValueError(f"{prefix} must be research_only=true")
        if row.get("automatic_execution_allowed") is not False:
            raise ValueError(f"{prefix} automatic_execution_allowed must be false")

    if _num(row.get("confidence"), -1) < 0 or _num(row.get("confidence"), -1) > 1:
        raise ValueError(f"{prefix} confidence must be 0..1")
    risks = row.get("risks")
    if not isinstance(risks, list):
        raise ValueError(f"{prefix} risks must be a list")


def validate_agent_report(report: dict, *, provider: str | None = None, cycle_id: str | None = None,
                          source_commit: str | None = None) -> dict:
    if not isinstance(report, dict):
        raise ValueError("source research report must be an object")
    p = str(report.get("provider") or "").lower().strip()
    if p not in PROVIDERS:
        raise ValueError("unsupported provider")
    if provider and p != provider.lower():
        raise ValueError("provider mismatch")
    if str(report.get("scope") or "") != "STRATEGY_SOURCE_RESEARCH":
        raise ValueError("scope must be STRATEGY_SOURCE_RESEARCH")
    if report.get("research_only") is not True or report.get("no_live_changes") is not True:
        raise ValueError("source reports must be research_only and no_live_changes")
    if str(report.get("status") or "") not in AGENT_STATUSES:
        raise ValueError("invalid agent status")
    cycle = str(report.get("cycle_id") or "").strip()
    source = str(report.get("source_commit") or "").strip()
    if not cycle or not source:
        raise ValueError("cycle_id and source_commit are required")
    if cycle_id and cycle != str(cycle_id):
        raise ValueError("cycle_id mismatch")
    if source_commit and source != str(source_commit):
        raise ValueError("source_commit mismatch")
    rows = report.get("source_recommendations")
    if not isinstance(rows, list):
        raise ValueError("source_recommendations must be a list")
    seen = set()
    for idx, row in enumerate(rows):
        _validate_source_row(row, prefix=f"source_recommendations[{idx}]", enforce_operational_flags=True)
        sid = str(row.get("id") or "").strip()
        if sid in seen:
            raise ValueError("source recommendation IDs must be unique")
        seen.add(sid)
    rejected = report.get("rejected_sources")
    if not isinstance(rejected, list):
        raise ValueError("rejected_sources must be a list")
    return report


def validate_master_decision(decision: dict, *, cycle_id: str | None = None,
                             source_commit: str | None = None) -> dict:
    if not isinstance(decision, dict):
        raise ValueError("source master decision must be an object")
    if str(decision.get("scope") or "") != "STRATEGY_SOURCE_MASTER":
        raise ValueError("scope must be STRATEGY_SOURCE_MASTER")
    for key, expected in (("cycle_id", cycle_id), ("source_commit", source_commit)):
        value = str(decision.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} is required")
        if expected and value != str(expected):
            raise ValueError(f"{key} mismatch")
    if decision.get("no_live_changes") is not True or decision.get("research_only") is not True:
        raise ValueError("source master must be research_only and no_live_changes")
    rows = decision.get("source_decisions")
    if not isinstance(rows, list):
        raise ValueError("source_decisions must be a list")
    for idx, row in enumerate(rows):
        # Keep structural/schema validation strict, but allow unsafe row-level
        # operational booleans to reach enforce_source_policy(), which rejects them
        # deterministically and records the reason instead of aborting the workflow.
        _validate_source_row(row, prefix=f"source_decisions[{idx}]", enforce_operational_flags=False)
        if str(row.get("disposition") or "") not in DISPOSITIONS:
            raise ValueError("invalid source disposition")
        agents = row.get("supporting_agents")
        if not isinstance(agents, list) or any(str(x).lower() not in PROVIDERS for x in agents):
            raise ValueError("unsupported supporting agent")
    return decision


def enforce_source_policy(decision: dict) -> dict:
    """Approve research sources only after independent support and conservative checks."""
    validate_master_decision(decision)
    approved = []
    gated = []
    for raw in decision.get("source_decisions") or []:
        row = dict(raw)
        agents = sorted({str(x).lower() for x in row.get("supporting_agents") or [] if str(x).lower() in PROVIDERS})
        confidence = _num(row.get("confidence"), 0)
        reasons = []
        eligible = str(row.get("disposition") or "") == "ACCEPT"
        if len(agents) < 2:
            eligible = False
            reasons.append("requires support from at least two independent agents")
        if confidence < 0.85:
            eligible = False
            reasons.append("confidence below 0.85")
        if str(row.get("source_class") or "") not in SOURCE_CLASSES:
            eligible = False
            reasons.append("unsupported source class")
        if not _https(row.get("official_url")):
            eligible = False
            reasons.append("canonical HTTPS URL required")
        if row.get("research_only") is not True or row.get("automatic_execution_allowed") is not False:
            eligible = False
            reasons.append("source must remain research-only with no automatic execution")
        row["supporting_agents"] = agents
        row["policy_approved"] = bool(eligible)
        row["policy_reasons"] = reasons or (["source research policy satisfied"] if eligible else ["not accepted"])
        gated.append(row)
        if eligible:
            approved.append({
                key: row.get(key)
                for key in (
                    "id", "name", "source_class", "official_url", "publisher", "chain_scope",
                    "access_model", "intended_use", "trust_basis", "risks", "confidence",
                )
            })
    out = dict(decision)
    out["source_decisions"] = gated
    out["approved_sources"] = approved
    out["approved_source_count"] = len(approved)
    out["policy"] = {
        "minimum_independent_agents": 2,
        "minimum_confidence": 0.85,
        "research_only": True,
        "automatic_execution": False,
        "automatic_package_install": False,
        "automatic_external_code_execution": False,
        "automatic_live_changes": False,
    }
    out["research_only"] = True
    out["no_live_changes"] = True
    return out


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("validate-agent")
    a.add_argument("--input", required=True)
    a.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    a.add_argument("--cycle-id", required=True)
    a.add_argument("--source-commit", required=True)
    m = sub.add_parser("gate-master")
    m.add_argument("--input", required=True)
    m.add_argument("--output", required=True)
    m.add_argument("--cycle-id", required=True)
    m.add_argument("--source-commit", required=True)
    args = p.parse_args()
    if args.cmd == "validate-agent":
        validate_agent_report(_load(args.input), provider=args.provider, cycle_id=args.cycle_id,
                              source_commit=args.source_commit)
        return 0
    payload = _load(args.input)
    validate_master_decision(payload, cycle_id=args.cycle_id, source_commit=args.source_commit)
    gated = enforce_source_policy(payload)
    Path(args.output).write_text(json.dumps(gated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("approved=" + str(gated.get("approved_source_count") or 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
