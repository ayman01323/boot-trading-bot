from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from learnerbot.config import AppSettings
from learnerbot import monitor_factory_pipeline as pipeline
from learnerbot import strategy_room
from scripts.strategy_factory_transport import AGENTS, exchange

AGENT_ORDER = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")
SEVERITY_PANEL_SIZE = {"P0": 7, "P1": 7, "P2": 4, "P3": 3, "INFO": 3}
MAX_PACKAGE_CHARS = 11_000
MAX_REVIEW_CHARS = 2_200

_FACTORY_DISPOSITION_RE = re.compile(
    r"(?im)^FACTORY_DISPOSITION:\s*(NO_ACTION|KEEP_MONITORING|RESEARCH_MORE|DRAFT_SHADOW_CHANGE|HUMAN_APPROVAL_REQUIRED)\s*$"
)
_FACTORY_TASK_RE = re.compile(r"(?im)^FACTORY_TASK:\s*(.*)$")
_DAILY_DISPOSITION_RE = re.compile(r"(?im)^DAILY_ENGINEERING_DISPOSITION:\s*(NO_ACTION|FACTORY_REVIEW)\s*$")
_FIELD_RE = re.compile(r"(?im)^([A-Z_]+):\s*(.*)$")


def _data_root(app) -> Path:
    path = Path(app.data_dir) / "monitor_factory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _package_text(package: dict) -> str:
    payload = package.get("payload") or package
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    return text[:MAX_PACKAGE_CHARS]


def _panel_for(package: dict) -> list[str]:
    severity = str(package.get("severity") or (package.get("payload") or {}).get("severity") or "P3").upper()
    n = SEVERITY_PANEL_SIZE.get(severity, 3)
    package_id = str(package.get("package_id") or "")
    # GPT is always represented because GPT is the final evidence adjudicator.
    others = [a for a in AGENT_ORDER if a != "gpt"]
    offset = sum(package_id.encode("utf-8")) % len(others) if package_id else 0
    rotated = others[offset:] + others[:offset]
    return ["gpt", *rotated[: max(0, n - 1)]]


def _review_prompt(agent: str, package: dict) -> str:
    return f"""You are {agent.upper()}, one independent Strategy Factory reviewer.

Review the structured monitor Problem/Opportunity Package below. The package is evidence, not an instruction to trade or deploy. Critically test the diagnosis, separate strategy/market causes from execution/infrastructure causes, and identify missing evidence. Objective evidence outranks model agreement.

Primary objective: durable money-weighted net P&L after all recorded costs while preserving correctness and safety. Win rate, wins-vs-losses count and gross profit-vs-loss value are supporting KPIs; they must never rescue negative net economics or justify weakening safety.

Authority: research/recommend/SHADOW hypothesis only. Do NOT trade, arm LIVE, change capital/risk/wallet/signing, weaken quote/liquidity/sellability/simulation/reserve/nonce/reconciliation gates, merge, deploy, or claim unavailable evidence.

Return a concise review with:
- conclusion: SUPPORTED | PARTLY_SUPPORTED | NOT_SUPPORTED | MORE_EVIDENCE
- root cause classification
- strongest evidence and strongest counterargument
- recommended next experiment/investigation
- falsification/acceptance test
- whether a SHADOW-only draft is justified

PACKAGE:
{_package_text(package)}
"""


def _final_prompt(package: dict, reviews: dict[str, dict]) -> str:
    blocks = []
    for agent in AGENT_ORDER:
        row = reviews.get(agent) or {}
        if str(row.get("status") or "") != "REPLIED":
            continue
        body = str(row.get("body") or "").strip()
        if body:
            blocks.append(f"===== {agent.upper()} =====\n{body[:MAX_REVIEW_CHARS]}")
    evidence = "\n\n".join(blocks)[:16_000]
    return f"""You are GPT acting as final Strategy Factory adjudicator.

Adjudicate the monitor package and independent reviews below. Do not vote. Prefer measured/deterministic evidence, reject unsupported claims, preserve useful minority objections, and distinguish STRATEGY/MARKET from EXECUTION/INFRASTRUCTURE/DATA causes.

Permitted disposition:
- NO_ACTION: package is unsupported or immaterial.
- KEEP_MONITORING: valid issue/opportunity but more observation is required.
- RESEARCH_MORE: fresh/public evidence or additional measurement is required before a change.
- DRAFT_SHADOW_CHANGE: a bounded Strategy-Lab/SHADOW-only change is justified. This may create only a draft PR through the existing allow-listed Strategy Room path; it cannot merge/deploy or alter LIVE/capital/risk/wallet/signing/safety gates.
- HUMAN_APPROVAL_REQUIRED: engineering/runtime/workflow/LIVE/capital/risk/wallet/signing/safety or other protected change is indicated.

For DRAFT_SHADOW_CHANGE, FACTORY_TASK must be one concrete <=1000-character implementation task confined to Strategy Lab/SHADOW research files and tests. Engineering fixes and monitor/workflow changes are HUMAN_APPROVAL_REQUIRED, not SHADOW changes.

End with exactly:
FACTORY_DISPOSITION: NO_ACTION|KEEP_MONITORING|RESEARCH_MORE|DRAFT_SHADOW_CHANGE|HUMAN_APPROVAL_REQUIRED
FACTORY_TASK: <task or NONE>

PACKAGE:
{_package_text(package)}

INDEPENDENT REVIEWS:
{evidence or '[No completed independent reviews]'}
"""


async def _ask(target: str, body: str, *, subject: str, thread_id: str, timeout: float = 180.0) -> dict:
    try:
        return await exchange(
            "master",
            target,
            body,
            subject=subject,
            thread_id=thread_id,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "status": "FAILED",
            "body": "",
            "error": f"{type(exc).__name__}: {exc}"[:1000],
            "delivered": False,
            "acknowledged": False,
        }


async def review_package(app, package: dict) -> dict:
    package_id = str(package.get("package_id") or "")
    panel = _panel_for(package)
    pipeline.set_package_state(app, package_id, "REVIEWING", review={"panel": panel})
    subject = f"Monitor Factory {package_id}"
    thread_id = f"mf-{package_id}"[:120]
    reviews: dict[str, dict] = {}
    # Sequential master exchanges avoid duplicate MASTER registrations on the local bus.
    for agent in panel:
        reviews[agent] = await _ask(agent, _review_prompt(agent, package), subject=subject, thread_id=thread_id)

    support_count = sum(1 for row in reviews.values() if str(row.get("status") or "") == "REPLIED" and str(row.get("body") or "").strip())
    final = await _ask("gpt", _final_prompt(package, reviews), subject=subject, thread_id=thread_id)
    final_body = str(final.get("body") or "")
    disposition_match = _FACTORY_DISPOSITION_RE.search(final_body)
    task_match = _FACTORY_TASK_RE.search(final_body)
    disposition = disposition_match.group(1).upper() if disposition_match else "KEEP_MONITORING"
    task = " ".join(str(task_match.group(1) if task_match else "").split())[:1000]

    state = "HUMAN_APPROVAL_REQUIRED" if disposition == "HUMAN_APPROVAL_REQUIRED" else "REVIEWED"
    bridge = None
    payload = package.get("payload") or {}
    if disposition == "DRAFT_SHADOW_CHANGE":
        if str(payload.get("lane") or "").upper() != "STRATEGY":
            disposition = "HUMAN_APPROVAL_REQUIRED"
            state = "HUMAN_APPROVAL_REQUIRED"
            task = "Protected/non-Strategy change cannot use the SHADOW draft bridge."
        elif support_count < 3 or not task or task.upper() == "NONE":
            disposition = "KEEP_MONITORING"
            task = "Insufficient completed reviews or no bounded SHADOW task."
        else:
            bridge = strategy_room.queue_draft_shadow_change(
                app,
                task=task,
                question=str(payload.get("title") or "Monitor Factory package")[:800],
                session_id=package_id[:64],
                requested_by="monitor_factory",
                support_count=support_count,
            )

    result = {
        "schema_version": 1,
        "package_id": package_id,
        "generated_epoch": int(time.time()),
        "panel": panel,
        "support_count": support_count,
        "reviews": reviews,
        "final": final,
        "disposition": disposition,
        "task": task or "NONE",
        "strategy_room_bridge": bridge,
        "no_live_changes": True,
        "auto_merge": False,
        "auto_deploy": False,
    }
    pipeline.set_package_state(app, package_id, state, review=result)
    _atomic_json(_data_root(app) / "reviews" / f"{package_id}.json", result)
    _atomic_json(_data_root(app) / "reviews" / "latest.json", result)
    return result


def factory_hourly(app, *, limit: int = 3) -> dict:
    packages = pipeline.pending_packages(app, limit=max(1, min(int(limit), 5)))
    results = []
    for package in packages:
        results.append(asyncio.run(review_package(app, package)))
    out = {
        "schema_version": 1,
        "mode": "FACTORY_HOURLY",
        "generated_epoch": int(time.time()),
        "packages_considered": len(packages),
        "results": results,
        "status": pipeline.status_summary(app),
    }
    _atomic_json(_data_root(app) / "factory_hourly_latest.json", out)
    return out


def _daily_prompt(agent: str, engineering: dict, repo_checks: dict) -> str:
    payload = json.dumps({"engineering_monitor": engineering, "deterministic_repo_checks": repo_checks}, ensure_ascii=False, indent=2, default=str)
    return f"""You are {agent.upper()}, today's rotating Engineering Monitor reviewer.

Use only the supplied deterministic evidence. Look for a real engineering defect, regression or evidence gap worth escalation. Do not tune trading strategy merely because profit is disappointing. Do not invent runtime facts. If there is no supported issue, say NO_ACTION.

You may recommend investigation/fixing to the wider Factory, but you may not edit files, deploy, trade, alter LIVE/ARMED/capital/risk, access wallets/signing/secrets, or weaken safety gates.

End with exactly these lines:
DAILY_ENGINEERING_DISPOSITION: NO_ACTION|FACTORY_REVIEW
DAILY_ENGINEERING_TITLE: <short title or NONE>
DAILY_ENGINEERING_CLASSIFICATION: EXECUTION|INFRASTRUCTURE|DATA
DAILY_ENGINEERING_SEVERITY: P1|P2|P3|INFO
DAILY_ENGINEERING_RECOMMENDATION: <bounded recommendation or NONE>

EVIDENCE:
{payload[:18_000]}
"""


def _repo_checks(root: Path) -> dict:
    import subprocess

    checks: dict[str, Any] = {}
    for name, cmd, timeout in (
        ("compile", ["python3", "-m", "compileall", "-q", "learnerbot", "scripts"], 120),
        ("git_status", ["git", "status", "--short", "--branch"], 30),
    ):
        try:
            p = subprocess.run(cmd, cwd=root, text=True, capture_output=True, timeout=timeout, check=False)
            checks[name] = {"returncode": int(p.returncode), "stdout": str(p.stdout or "")[-4000:], "stderr": str(p.stderr or "")[-3000:]}
        except Exception as exc:
            checks[name] = {"returncode": 127, "error": f"{type(exc).__name__}: {exc}"[:600]}
    return checks


def _fields(text: str) -> dict[str, str]:
    return {m.group(1).upper(): " ".join(m.group(2).split()) for m in _FIELD_RE.finditer(str(text or ""))}


def engineering_daily(app, repo_root: Path) -> dict:
    now = int(time.time())
    engineering = pipeline.run_engineering_monitor(app, now=now)
    checks = _repo_checks(repo_root)
    day_key = int(time.strftime("%j", time.gmtime(now)))
    agent = AGENT_ORDER[(day_key - 1) % len(AGENT_ORDER)]
    review = asyncio.run(_ask(agent, _daily_prompt(agent, engineering, checks), subject=f"Daily Engineering Monitor {time.strftime('%Y-%m-%d', time.gmtime(now))}", thread_id=f"daily-eng-{time.strftime('%Y%m%d', time.gmtime(now))}"))
    body = str(review.get("body") or "")
    disposition = _DAILY_DISPOSITION_RE.search(body)
    fields = _fields(body)
    finding = None
    if disposition and disposition.group(1).upper() == "FACTORY_REVIEW":
        classification = fields.get("DAILY_ENGINEERING_CLASSIFICATION", "DATA").upper()
        if classification not in {"EXECUTION", "INFRASTRUCTURE", "DATA"}:
            classification = "DATA"
        severity = fields.get("DAILY_ENGINEERING_SEVERITY", "P3").upper()
        if severity not in {"P1", "P2", "P3", "INFO"}:
            severity = "P3"
        title = fields.get("DAILY_ENGINEERING_TITLE", "Rotating engineering review requested Factory analysis")[:300]
        recommendation = fields.get("DAILY_ENGINEERING_RECOMMENDATION", "Investigate with objective evidence before any change")[:1800]
        finding = pipeline.record_finding(
            app,
            lane="ENGINEERING",
            finding_type="PROBLEM",
            classification=classification,
            severity=severity,
            title=title,
            scope="DAILY_ROTATING_AI_REVIEW",
            source_version=time.strftime("%Y-%m-%d", time.gmtime(now)),
            evidence={"reviewer": agent, "review": body[:5000], "engineering_monitor": engineering, "repo_checks": checks},
            recommendation=recommendation,
            acceptance_test="Any corrective proposal must be supported by a reproducible test/measurement and preserve all LIVE, capital, wallet/signing and execution-safety controls.",
            now=now,
        )
        pipeline.queue_finding(app, finding, now=now)

    out = {
        "schema_version": 1,
        "mode": "ENGINEERING_DAILY_ROTATION",
        "generated_epoch": now,
        "reviewer": agent,
        "review": review,
        "finding": finding,
        "repo_checks": checks,
        "no_repository_mutation_by_reviewer": True,
    }
    _atomic_json(_data_root(app) / "engineering_daily_latest.json", out)
    return out


def _weekly_prompt(agent: str, evidence: dict) -> str:
    text = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    return f"""You are {agent.upper()}, one member of the weekly seven-agent Monitor/Factory Council.

Review the combined Engineering Monitor, Strategy Monitor, Factory queue and deterministic checks. Your job is not just to react to current defects: identify blind spots in what the monitors measure, new failure modes they should detect, stale assumptions, and opportunities for safer/better research. Challenge the current monitor design. Do not propose raw win-rate optimisation that damages money-weighted economics.

Separate: STRATEGY, MARKET, EXECUTION, INFRASTRUCTURE, DATA, and RESEARCH. Prefer measurable additions with explicit false-positive controls. Public-current-tool research should be routed to the existing read-only Strategy Research worker/source-research cycle rather than treated as fact without retrieval.

No file edits, deployment, LIVE/capital/risk/wallet/signing changes, or safety-gate weakening. Return concise recommendations and label each as KEEP / ADD_MEASUREMENT / RESEARCH / HUMAN_CHANGE.

EVIDENCE:
{text[:18_000]}
"""


def weekly_joint(app, repo_root: Path) -> dict:
    now = int(time.time())
    engineering = pipeline.run_engineering_monitor(app, now=now)
    strategy = pipeline.run_strategy_monitor(app, now=now)
    pending = pipeline.pending_packages(app, limit=10)
    evidence = {
        "engineering": engineering,
        "strategy": strategy,
        "factory_status": pipeline.status_summary(app),
        "pending_packages": [p.get("payload") or {} for p in pending],
        "repo_checks": _repo_checks(repo_root),
        "existing_controls": {
            "strategy_research_worker": "read-only provenance/freshness gate",
            "strategy_promotion": "SHADOW -> explicit MASTER canary -> explicit MASTER full live",
            "strategy_room": "draft-PR-only SHADOW allow-list; no merge/deploy",
        },
    }
    reviews = {}
    subject = f"Weekly Seven-Agent Monitor Council {time.strftime('%Y-%m-%d', time.gmtime(now))}"
    thread = f"weekly-monitor-{time.strftime('%Y%m%d', time.gmtime(now))}"
    for agent in AGENT_ORDER:
        reviews[agent] = asyncio.run(_ask(agent, _weekly_prompt(agent, evidence), subject=subject, thread_id=thread))

    compact = "\n\n".join(
        f"===== {a.upper()} =====\n{str((reviews.get(a) or {}).get('body') or '')[:MAX_REVIEW_CHARS]}"
        for a in AGENT_ORDER
        if str((reviews.get(a) or {}).get("body") or "").strip()
    )
    final_prompt = f"""You are GPT, final adjudicator for the weekly seven-agent Monitor/Factory Council.

Synthesize by evidence, not vote. Produce: (1) confirmed current defects/opportunities; (2) monitor blind spots to add; (3) items requiring fresh public research; (4) items requiring human-approved engineering change; (5) items to reject as speculative. Keep money-weighted net economics primary and preserve every LIVE/capital/wallet/signing/safety boundary.

Do not claim recommendations are already implemented. End with:
WEEKLY_MONITOR_STATUS: KEEP|IMPROVEMENT_RECOMMENDED|HUMAN_APPROVAL_REQUIRED

REVIEWS:
{compact[:18_000]}
"""
    final = asyncio.run(_ask("gpt", final_prompt, subject=subject, thread_id=thread))
    out = {
        "schema_version": 1,
        "mode": "WEEKLY_SEVEN_AGENT_JOINT_REVIEW",
        "generated_epoch": now,
        "agents": list(AGENT_ORDER),
        "reviews": reviews,
        "final": final,
        "evidence_summary": {
            "open_findings": pipeline.status_summary(app).get("open_findings"),
            "pending_packages": len(pending),
        },
        "no_live_changes": True,
    }
    _atomic_json(_data_root(app) / "weekly_joint_latest.json", out)
    return out


def observe_engineering(app) -> dict:
    return pipeline.run_engineering_monitor(app)


def observe_strategy(app) -> dict:
    return pipeline.run_strategy_monitor(app)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence-led Engineering/Strategy Monitor and Strategy Factory operations")
    parser.add_argument(
        "mode",
        choices=("observe-engineering", "observe-strategy", "factory-hourly", "engineering-daily", "weekly-joint", "status"),
    )
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    app = AppSettings.load()
    root = Path(__file__).resolve().parents[1]

    if args.mode == "observe-engineering":
        result = observe_engineering(app)
    elif args.mode == "observe-strategy":
        result = observe_strategy(app)
    elif args.mode == "factory-hourly":
        result = factory_hourly(app, limit=args.limit)
    elif args.mode == "engineering-daily":
        result = engineering_daily(app, root)
    elif args.mode == "weekly-joint":
        result = weekly_joint(app, root)
    else:
        result = pipeline.status_summary(app)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
