from __future__ import annotations

import argparse
import json
from pathlib import Path

from learnerbot import ai_agent_target_score as score


def _json(value: str) -> dict:
    data = json.loads(value or "{}")
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Record/audit/report focused V4 AI target contribution scores")
    parser.add_argument("--data-dir", default="data")
    sub = parser.add_subparsers(dest="command", required=True)

    pending = sub.add_parser("pending")
    pending.add_argument("--agent", required=True)
    pending.add_argument("--contribution-id", required=True)
    pending.add_argument("--category", required=True)
    pending.add_argument("--case-id", default="")
    pending.add_argument("--source-sha", default="")
    pending.add_argument("--role", default="")

    record = sub.add_parser("record")
    record.add_argument("--agent", required=True)
    record.add_argument("--contribution-id", required=True)
    record.add_argument("--scorer", required=True)
    record.add_argument("--category", required=True)
    record.add_argument("--scores-json", required=True, type=_json)
    record.add_argument("--economic-json", default="{}", type=_json)
    record.add_argument("--penalties-json", default="{}", type=_json)
    record.add_argument("--outcome-resolved", action="store_true")
    record.add_argument("--material", action="store_true")
    record.add_argument("--unsafe-flag", action="store_true")
    record.add_argument("--case-id", default="")
    record.add_argument("--source-sha", default="")
    record.add_argument("--notes", default="")

    audit = sub.add_parser("audit")
    audit.add_argument("--contribution-id", required=True)
    audit.add_argument("--auditor", required=True)
    audit.add_argument("--status", choices=("PASS", "CORRECTED", "REJECTED"), default="PASS")
    audit.add_argument("--audited-score", type=float)
    audit.add_argument("--notes", default="")

    value = sub.add_parser("value")
    value.add_argument("--agent", required=True)
    value.add_argument("--assessor", required=True)
    value.add_argument("--dimensions-json", required=True, type=_json)
    value.add_argument("--evidence-window-days", required=True, type=int)
    value.add_argument("--material-outcomes", required=True, type=int)
    value.add_argument("--consecutive-weak-windows", type=int, default=0)
    value.add_argument("--ablation-passed", action="store_true")
    value.add_argument("--no-unique-critical-specialization", action="store_true")
    value.add_argument("--independently-audited", action="store_true")
    value.add_argument("--notes", default="")

    sub.add_parser("summary")
    args = parser.parse_args()
    base = Path(args.data_dir)

    if args.command == "pending":
        result = score.register_pending(
            base,
            agent=args.agent,
            contribution_id=args.contribution_id,
            category=args.category,
            case_id=args.case_id,
            source_sha=args.source_sha,
            role=args.role,
        )
    elif args.command == "record":
        result = score.record_score(
            base,
            agent=args.agent,
            contribution_id=args.contribution_id,
            scorer=args.scorer,
            category=args.category,
            scores=args.scores_json,
            economic=args.economic_json,
            penalties=args.penalties_json,
            outcome_resolved=args.outcome_resolved,
            material=args.material,
            unsafe_flag=args.unsafe_flag,
            case_id=args.case_id,
            source_sha=args.source_sha,
            notes=args.notes,
        )
    elif args.command == "audit":
        result = score.audit_score(
            base,
            contribution_id=args.contribution_id,
            auditor=args.auditor,
            status=args.status,
            audited_score=args.audited_score,
            notes=args.notes,
        )
    elif args.command == "value":
        result = score.record_value_assessment(
            base,
            agent=args.agent,
            assessor=args.assessor,
            dimensions=args.dimensions_json,
            evidence_window_days=args.evidence_window_days,
            material_outcomes=args.material_outcomes,
            consecutive_weak_windows=args.consecutive_weak_windows,
            ablation_passed=args.ablation_passed,
            no_unique_critical_specialization=args.no_unique_critical_specialization,
            independently_audited=args.independently_audited,
            notes=args.notes,
        )
    else:
        result = score.summary(base)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
