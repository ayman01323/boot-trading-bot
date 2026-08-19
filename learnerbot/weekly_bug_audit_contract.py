from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


AGENT_PROVIDERS = {"gpt", "gemini", "copilot"}
SEVERITIES = {"P0", "P1", "P2", "P3"}
DISPOSITIONS = {"ACCEPT", "REJECT", "DEFER"}
MASTER_STATUSES = {"NO_ACTION", "DRAFT_FIX", "HUMAN_REVIEW_REQUIRED"}
RISK_CLASSES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
PROTECTED_PATH_PATTERNS = (
    re.compile(r"(^|/)\.env($|\.)", re.I),
    re.compile(r"(^|/)(id_rsa|.*private.*key.*|.*seed.*|.*mnemonic.*)$", re.I),
    re.compile(r"^\.github/workflows/(deploy-vps|server-ops)\.ya?ml$", re.I),
    re.compile(r"(^|/)(wallet|signing|credential|secret)[^/]*\.(json|ya?ml|csv|txt)$", re.I),
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _clean_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lstrip("./")


def protected_path(path: str) -> bool:
    cleaned = _clean_path(path)
    return any(pattern.search(cleaned) for pattern in PROTECTED_PATH_PATTERNS)


def validate_agent_report(report: dict, *, provider: str | None = None, source_commit: str | None = None) -> dict:
    if not isinstance(report, dict):
        raise ValueError("agent report must be a JSON object")
    p = str(report.get("provider") or "").lower().strip()
    if provider and p != provider.lower().strip():
        raise ValueError(f"provider mismatch: expected {provider}, got {p or 'missing'}")
    if p not in AGENT_PROVIDERS:
        raise ValueError(f"unsupported provider: {p or 'missing'}")
    commit = str(report.get("source_commit") or "").strip()
    if not commit:
        raise ValueError("source_commit is required")
    if source_commit and commit != str(source_commit).strip():
        raise ValueError("source_commit mismatch")
    if str(report.get("scope") or "") != "FULL_REPOSITORY_BUG_AUDIT":
        raise ValueError("scope must be FULL_REPOSITORY_BUG_AUDIT")
    if report.get("report_only") is not True:
        raise ValueError("agent report must be report_only=true")
    if report.get("no_live_changes") is not True:
        raise ValueError("agent report must be no_live_changes=true")
    status = str(report.get("status") or "")
    if status not in {"CLEAN", "ISSUES_FOUND", "INCOMPLETE"}:
        raise ValueError("invalid agent report status")
    findings = report.get("findings")
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    seen: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"finding {index} must be an object")
        finding_id = str(finding.get("id") or "").strip()
        if not finding_id or finding_id in seen:
            raise ValueError(f"finding {index} has missing/duplicate id")
        seen.add(finding_id)
        if str(finding.get("severity") or "") not in SEVERITIES:
            raise ValueError(f"finding {finding_id} has invalid severity")
        confidence = _num(finding.get("confidence"), -1)
        if confidence < 0 or confidence > 1:
            raise ValueError(f"finding {finding_id} confidence must be 0..1")
        evidence = finding.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"finding {finding_id} must contain evidence")
        for ev in evidence:
            if not isinstance(ev, dict) or not str(ev.get("file") or "").strip():
                raise ValueError(f"finding {finding_id} evidence must identify a file")
        tests = finding.get("tests_required")
        if not isinstance(tests, list):
            raise ValueError(f"finding {finding_id} tests_required must be a list")
    return report


def validate_master_decision(decision: dict, *, source_commit: str | None = None) -> dict:
    if not isinstance(decision, dict):
        raise ValueError("master decision must be a JSON object")
    if str(decision.get("source_commit") or "").strip() == "":
        raise ValueError("master decision source_commit is required")
    if source_commit and str(decision.get("source_commit")) != str(source_commit):
        raise ValueError("master decision source_commit mismatch")
    if str(decision.get("status") or "") not in MASTER_STATUSES:
        raise ValueError("invalid master status")
    if decision.get("live_auto_deploy") is not False:
        raise ValueError("live_auto_deploy must be false")
    if decision.get("draft_pr_only") is not True:
        raise ValueError("draft_pr_only must be true")
    rows = decision.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("master decisions must be a list")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("master decision row must be an object")
        if str(row.get("disposition") or "") not in DISPOSITIONS:
            raise ValueError("invalid disposition")
        if str(row.get("severity") or "") not in SEVERITIES:
            raise ValueError("invalid severity")
        if str(row.get("risk_class") or "") not in RISK_CLASSES:
            raise ValueError("invalid risk_class")
        confidence = _num(row.get("confidence"), -1)
        if confidence < 0 or confidence > 1:
            raise ValueError("master decision confidence must be 0..1")
        agents = row.get("supporting_agents") or []
        if not isinstance(agents, list) or any(str(a).lower() not in AGENT_PROVIDERS for a in agents):
            raise ValueError("supporting_agents contains an unsupported provider")
        allowed = row.get("allowed_files") or []
        if not isinstance(allowed, list):
            raise ValueError("allowed_files must be a list")
        tests = row.get("required_tests") or []
        if not isinstance(tests, list):
            raise ValueError("required_tests must be a list")
    return decision


def enforce_master_policy(decision: dict) -> dict:
    """Apply a deterministic policy after GPT synthesis.

    GPT may propose a disposition, but this function independently decides whether an
    accepted item is eligible for automated implementation. P0 findings, HIGH/CRITICAL
    risk changes, protected-file changes, low confidence, and weakly corroborated claims
    are never acted on automatically.
    """
    validate_master_decision(decision)
    gated_rows: list[dict] = []
    accepted = 0
    human_required = False
    for raw in decision.get("decisions") or []:
        row = dict(raw)
        requested = str(row.get("disposition") or "DEFER")
        severity = str(row.get("severity") or "P3")
        risk_class = str(row.get("risk_class") or "HIGH")
        confidence = _num(row.get("confidence"), 0)
        agents = sorted({str(a).lower() for a in (row.get("supporting_agents") or []) if str(a).lower() in AGENT_PROVIDERS})
        deterministic = bool(row.get("deterministic_evidence"))
        allowed_files = [_clean_path(p) for p in (row.get("allowed_files") or []) if _clean_path(p)]
        required_tests = [str(t).strip() for t in (row.get("required_tests") or []) if str(t).strip()]
        reasons: list[str] = []

        eligible = requested == "ACCEPT"
        if severity == "P0":
            eligible = False
            human_required = True
            reasons.append("P0 requires human review")
        if risk_class in {"HIGH", "CRITICAL"}:
            eligible = False
            human_required = True
            reasons.append(f"{risk_class} risk requires human review")
        if confidence < 0.85:
            eligible = False
            reasons.append("confidence below 0.85")
        if len(agents) < 2 and not deterministic:
            eligible = False
            reasons.append("requires two independent agents or deterministic evidence")
        if deterministic and not required_tests:
            eligible = False
            reasons.append("deterministic claim requires an explicit verification test")
        if any(protected_path(path) for path in allowed_files):
            eligible = False
            human_required = True
            reasons.append("protected/high-risk file requires human review")
        if not allowed_files and requested == "ACCEPT":
            eligible = False
            reasons.append("accepted fix has no bounded allowed_files scope")
        if not required_tests and requested == "ACCEPT":
            eligible = False
            reasons.append("accepted fix has no required verification tests")

        row["policy_eligible"] = bool(eligible)
        row["policy_reasons"] = reasons or (["policy requirements satisfied"] if eligible else ["not accepted by GPT"])
        row["supporting_agents"] = agents
        row["allowed_files"] = allowed_files
        row["required_tests"] = required_tests
        gated_rows.append(row)
        if eligible:
            accepted += 1

    out = dict(decision)
    out["decisions"] = gated_rows
    out["policy"] = {
        "minimum_confidence": 0.85,
        "minimum_independent_agents": 2,
        "deterministic_evidence_can_substitute_for_second_agent": True,
        "deterministic_evidence_requires_explicit_test": True,
        "p0_auto_implementation": False,
        "high_or_critical_risk_auto_implementation": False,
        "protected_paths_auto_implementation": False,
        "required_tests_mandatory_for_accepted_fix": True,
    }
    out["implementation_allowed"] = accepted > 0 and not human_required
    out["policy_accepted_count"] = accepted
    if human_required:
        out["status"] = "HUMAN_REVIEW_REQUIRED"
        out["implementation_allowed"] = False
    elif accepted > 0:
        out["status"] = "DRAFT_FIX"
    elif out.get("status") == "DRAFT_FIX":
        out["status"] = "NO_ACTION"
    out["live_auto_deploy"] = False
    out["draft_pr_only"] = True
    return out


def extract_marked_json(text: str) -> dict:
    text = str(text or "")
    marker = re.search(r"WEEKLY_AUDIT_JSON_BEGIN\s*(\{.*?\})\s*WEEKLY_AUDIT_JSON_END", text, flags=re.S)
    if marker:
        return json.loads(marker.group(1))
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    if fenced:
        return json.loads(fenced.group(1))
    starts = [m.start() for m in re.finditer(r"\{", text)]
    decoder = json.JSONDecoder()
    for start in reversed(starts):
        try:
            obj, _ = decoder.raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    raise ValueError("no JSON object found in agent output")


def _cmd_validate_agent(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    validate_agent_report(payload, provider=args.provider, source_commit=args.source_commit)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    raw = Path(args.input).read_text(encoding="utf-8", errors="replace")
    payload = extract_marked_json(raw)
    validate_agent_report(payload, provider=args.provider, source_commit=args.source_commit)
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    validate_master_decision(payload, source_commit=args.source_commit)
    gated = enforce_master_policy(payload)
    Path(args.output).write_text(json.dumps(gated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("implementation_allowed=" + ("true" if gated.get("implementation_allowed") else "false"))
    print("status=" + str(gated.get("status") or "UNKNOWN"))
    print("accepted=" + str(gated.get("policy_accepted_count") or 0))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    validate = sub.add_parser("validate-agent")
    validate.add_argument("--input", required=True)
    validate.add_argument("--provider", required=True, choices=sorted(AGENT_PROVIDERS))
    validate.add_argument("--source-commit", required=True)
    validate.set_defaults(func=_cmd_validate_agent)

    extract = sub.add_parser("extract-agent")
    extract.add_argument("--input", required=True)
    extract.add_argument("--output", required=True)
    extract.add_argument("--provider", required=True, choices=sorted(AGENT_PROVIDERS))
    extract.add_argument("--source-commit", required=True)
    extract.set_defaults(func=_cmd_extract)

    gate = sub.add_parser("gate-master")
    gate.add_argument("--input", required=True)
    gate.add_argument("--output", required=True)
    gate.add_argument("--source-commit", required=True)
    gate.set_defaults(func=_cmd_gate)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
