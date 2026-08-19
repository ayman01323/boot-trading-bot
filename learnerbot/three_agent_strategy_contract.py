from __future__ import annotations

import argparse
import json
import re
from pathlib import PurePosixPath, Path
from typing import Any

PROVIDERS = {"gpt", "gemini", "copilot"}
AGENT_STATUSES = {"HEALTHY", "CHANGES_PROPOSED", "INCOMPLETE"}
STRATEGY_ACTIONS = {
    "KEEP", "IMPROVE", "REWORK", "SHADOW_MORE", "REPLACE", "DORMANT",
    "NEW_SHADOW", "ASSET_REQUEST", "RESEARCH_MORE",
}
DISPOSITIONS = {"ACCEPT", "REJECT", "DEFER"}
MASTER_STATUSES = {"NO_ACTION", "DRAFT_SHADOW_CHANGE", "HUMAN_REVIEW_REQUIRED"}
CODE_ACTIONS = {"IMPROVE", "REWORK", "SHADOW_MORE", "NEW_SHADOW"}

_ALLOWED_EXACT = {
    "learnerbot/strategy_lab.py",
    "learnerbot/strategy_lab_research.py",
    "learnerbot/strategy_ai_proposals.py",
    "learnerbot/cross_chain_strategy_signals.py",
    "learnerbot/market_feature_adapter.py",
    "learnerbot/shadow_strategy_executor.py",
    "tests/test_cross_chain_strategy_signals.py",
    "tests/test_market_feature_shadow_executor.py",
    "docs/STRATEGY_LAB.md",
}
_ALLOWED_PREFIXES = (
    "tests/test_strategy_",
    "tests/test_three_agent_strategy_",
)
_PROTECTED_RE = (
    re.compile(r"^\.github/workflows/", re.I),
    re.compile(r"(^|/)(live_executor|auto_trader|solana_live|wallet|signing|secret|credential)", re.I),
    re.compile(r"(^|/)(\.env|id_rsa|.*private.*key.*|.*seed.*|.*mnemonic.*)$", re.I),
    re.compile(r"(^|/)CSVbot/", re.I),
    re.compile(r"(^|/)contracts/", re.I),
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def clean_path(value: Any) -> str:
    p = str(value or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return str(PurePosixPath(p)) if p else ""


def strategy_auto_path_allowed(path: str) -> bool:
    p = clean_path(path)
    if not p or any(rx.search(p) for rx in _PROTECTED_RE):
        return False
    return p in _ALLOWED_EXACT or any(p.startswith(prefix) for prefix in _ALLOWED_PREFIXES)


def validate_agent_report(report: dict, *, provider: str | None = None, cycle_id: str | None = None,
                          source_commit: str | None = None, evidence_sha256: str | None = None) -> dict:
    if not isinstance(report, dict):
        raise ValueError("strategy agent report must be an object")
    p = str(report.get("provider") or "").lower().strip()
    if p not in PROVIDERS:
        raise ValueError("unsupported strategy provider")
    if provider and p != provider.lower():
        raise ValueError("provider mismatch")
    if str(report.get("scope") or "") != "THREE_AGENT_STRATEGY_REVIEW":
        raise ValueError("scope must be THREE_AGENT_STRATEGY_REVIEW")
    if report.get("review_only") is not True or report.get("no_live_changes") is not True:
        raise ValueError("independent strategy reports must be review_only and no_live_changes")
    status = str(report.get("status") or "")
    if status not in AGENT_STATUSES:
        raise ValueError("invalid strategy agent status")
    cycle = str(report.get("cycle_id") or "").strip()
    source = str(report.get("source_commit") or "").strip()
    evidence = str(report.get("evidence_sha256") or "").strip()
    if not cycle or not source or not evidence:
        raise ValueError("cycle_id, source_commit and evidence_sha256 are required")
    if cycle_id and cycle != str(cycle_id):
        raise ValueError("cycle_id mismatch")
    if source_commit and source != str(source_commit):
        raise ValueError("source_commit mismatch")
    if evidence_sha256 and evidence != str(evidence_sha256):
        raise ValueError("evidence_sha256 mismatch")
    proposals = report.get("proposals")
    if not isinstance(proposals, list):
        raise ValueError("proposals must be a list")
    seen = set()
    for idx, row in enumerate(proposals):
        if not isinstance(row, dict):
            raise ValueError(f"proposal {idx} must be an object")
        pid = str(row.get("id") or "").strip()
        if not pid or pid in seen:
            raise ValueError("proposal IDs must be non-empty and unique")
        seen.add(pid)
        action = str(row.get("action") or "")
        if action not in STRATEGY_ACTIONS:
            raise ValueError(f"proposal {pid} has invalid action")
        confidence = _num(row.get("confidence"), -1)
        if confidence < 0 or confidence > 1:
            raise ValueError(f"proposal {pid} confidence must be 0..1")
        chain_scope = row.get("chain_scope")
        if not isinstance(chain_scope, list) or not chain_scope:
            raise ValueError(f"proposal {pid} needs chain_scope")
        chains = {str(x).upper() for x in chain_scope}
        if not chains.issubset({"SOLANA", "EVM"}):
            raise ValueError(f"proposal {pid} has unsupported chain scope")
        evidence_rows = row.get("evidence")
        if not isinstance(evidence_rows, list) or not evidence_rows:
            raise ValueError(f"proposal {pid} needs evidence")
        if any(not isinstance(ev, dict) or not str(ev.get("path") or "").strip() for ev in evidence_rows):
            raise ValueError(f"proposal {pid} evidence must identify a path")
        if not str(row.get("shadow_test") or "").strip():
            raise ValueError(f"proposal {pid} needs a falsifiable shadow_test")
        files = row.get("suggested_files") or []
        if not isinstance(files, list):
            raise ValueError(f"proposal {pid} suggested_files must be a list")
    return report


def validate_master_decision(decision: dict, *, cycle_id: str | None = None,
                             source_commit: str | None = None, evidence_sha256: str | None = None) -> dict:
    if not isinstance(decision, dict):
        raise ValueError("strategy master decision must be an object")
    if str(decision.get("status") or "") not in MASTER_STATUSES:
        raise ValueError("invalid strategy master status")
    for key, expected in (
        ("cycle_id", cycle_id), ("source_commit", source_commit), ("evidence_sha256", evidence_sha256)
    ):
        value = str(decision.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} is required")
        if expected and value != str(expected):
            raise ValueError(f"{key} mismatch")
    if decision.get("live_auto_deploy") is not False or decision.get("draft_pr_only") is not True:
        raise ValueError("master strategy decision must be draft-only and no live auto-deploy")
    rows = decision.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("decisions must be a list")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("master decision row must be an object")
        if str(row.get("disposition") or "") not in DISPOSITIONS:
            raise ValueError("invalid disposition")
        if str(row.get("action") or "") not in STRATEGY_ACTIONS:
            raise ValueError("invalid strategy action")
        if _num(row.get("confidence"), -1) < 0 or _num(row.get("confidence"), -1) > 1:
            raise ValueError("master confidence must be 0..1")
        agents = row.get("supporting_agents") or []
        if not isinstance(agents, list) or any(str(a).lower() not in PROVIDERS for a in agents):
            raise ValueError("unsupported supporting agent")
        allowed = row.get("allowed_files") or []
        if not isinstance(allowed, list):
            raise ValueError("allowed_files must be a list")
        tests = row.get("required_tests") or []
        if not isinstance(tests, list):
            raise ValueError("required_tests must be a list")
    return decision


def enforce_master_policy(decision: dict) -> dict:
    """Deterministically restrict GPT strategy decisions to low-risk SHADOW-only edits."""
    validate_master_decision(decision)
    gated = []
    implementation_count = 0
    human_required = False
    for raw in decision.get("decisions") or []:
        row = dict(raw)
        requested = str(row.get("disposition") or "DEFER")
        action = str(row.get("action") or "KEEP")
        confidence = _num(row.get("confidence"), 0)
        risk = str(row.get("risk_class") or "HIGH").upper()
        agents = sorted({str(a).lower() for a in (row.get("supporting_agents") or []) if str(a).lower() in PROVIDERS})
        files = [clean_path(x) for x in (row.get("allowed_files") or []) if clean_path(x)]
        tests = [str(x).strip() for x in (row.get("required_tests") or []) if str(x).strip()]
        shadow_only = row.get("shadow_only") is True
        reasons = []
        eligible = requested == "ACCEPT" and action in CODE_ACTIONS

        if requested == "ACCEPT" and action not in CODE_ACTIONS:
            eligible = False
            reasons.append(f"{action} is a decision/research action, not an auto-code action")
        if confidence < 0.85:
            eligible = False
            reasons.append("confidence below 0.85")
        if len(agents) < 2:
            eligible = False
            reasons.append("requires support from at least two independent agents")
        if risk not in {"LOW", "MEDIUM"}:
            eligible = False
            human_required = True
            reasons.append("HIGH/CRITICAL strategy change requires human review")
        if not shadow_only:
            eligible = False
            human_required = True
            reasons.append("automatic strategy implementation must be shadow_only=true")
        if not files:
            eligible = False
            reasons.append("no bounded allowed_files scope")
        elif any(not strategy_auto_path_allowed(p) for p in files):
            eligible = False
            human_required = True
            reasons.append("one or more files are outside the Strategy Lab SHADOW allow-list")
        if not tests:
            eligible = False
            reasons.append("accepted strategy code change needs explicit required tests")

        row["supporting_agents"] = agents
        row["allowed_files"] = files
        row["policy_eligible"] = bool(eligible)
        row["policy_reasons"] = reasons or (["strategy SHADOW policy requirements satisfied"] if eligible else ["not accepted for auto-code"])
        gated.append(row)
        if eligible:
            implementation_count += 1

    out = dict(decision)
    out["decisions"] = gated
    out["policy"] = {
        "minimum_confidence": 0.85,
        "minimum_independent_agents": 2,
        "risk_allowed": ["LOW", "MEDIUM"],
        "shadow_only_required": True,
        "live_execution_files_allowed": False,
        "auto_merge": False,
        "auto_deploy": False,
    }
    out["policy_accepted_count"] = implementation_count
    out["implementation_allowed"] = implementation_count > 0 and not human_required
    if human_required:
        out["status"] = "HUMAN_REVIEW_REQUIRED"
        out["implementation_allowed"] = False
    elif implementation_count:
        out["status"] = "DRAFT_SHADOW_CHANGE"
    elif out.get("status") == "DRAFT_SHADOW_CHANGE":
        out["status"] = "NO_ACTION"
    out["live_auto_deploy"] = False
    out["draft_pr_only"] = True
    return out


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("validate-agent")
    a.add_argument("--input", required=True)
    a.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    a.add_argument("--cycle-id", required=True)
    a.add_argument("--source-commit", required=True)
    a.add_argument("--evidence-sha256", required=True)
    m = sub.add_parser("gate-master")
    m.add_argument("--input", required=True)
    m.add_argument("--output", required=True)
    m.add_argument("--cycle-id", required=True)
    m.add_argument("--source-commit", required=True)
    m.add_argument("--evidence-sha256", required=True)
    args = parser.parse_args()
    if args.cmd == "validate-agent":
        payload = _load(args.input)
        validate_agent_report(payload, provider=args.provider, cycle_id=args.cycle_id,
                              source_commit=args.source_commit, evidence_sha256=args.evidence_sha256)
        return 0
    payload = _load(args.input)
    validate_master_decision(payload, cycle_id=args.cycle_id, source_commit=args.source_commit,
                             evidence_sha256=args.evidence_sha256)
    gated = enforce_master_policy(payload)
    Path(args.output).write_text(json.dumps(gated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("implementation_allowed=" + ("true" if gated.get("implementation_allowed") else "false"))
    print("status=" + str(gated.get("status") or "UNKNOWN"))
    print("accepted=" + str(gated.get("policy_accepted_count") or 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
