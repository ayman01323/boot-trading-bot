from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PROVIDERS = ("gpt", "gemini", "copilot", "claude")
PROVIDER_ORDER = ("gpt", "claude", "gemini", "copilot")
STRATEGY_SAFE_EXACT = {
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
PROTECTED_RE = (
    re.compile(r"^\.github/workflows/", re.I),
    re.compile(r"(^|/)(live_executor|auto_trader|solana_live|wallet|signing|secret|credential)", re.I),
    re.compile(r"(^|/)(\.env|id_rsa|.*private.*key.*|.*seed.*|.*mnemonic.*)$", re.I),
    re.compile(r"(^|/)CSVbot/", re.I),
    re.compile(r"(^|/)contracts/", re.I),
)


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _clean_path(v: Any) -> str:
    p = str(v or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _strategy_path_allowed(path: str) -> bool:
    p = _clean_path(path)
    if not p or any(rx.search(p) for rx in PROTECTED_RE):
        return False
    return p in STRATEGY_SAFE_EXACT or p.startswith("tests/test_strategy_") or p.startswith("tests/test_three_agent_strategy_") or p.startswith("tests/test_four_agent_strategy_")


def _engineering_path_allowed(path: str) -> bool:
    p = _clean_path(path)
    return bool(p) and not any(rx.search(p) for rx in PROTECTED_RE)


def _load_reports(folder: Path, lane: str, identity: str, source: str, evidence: str) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    for provider in PROVIDERS:
        path = folder / f"{provider}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(value, dict):
            continue
        if str(value.get("provider") or "").lower() != provider:
            continue
        status = str(value.get("status") or "").upper()
        if status in {"", "INCOMPLETE", "FAILED"}:
            continue
        if lane == "strategy":
            if str(value.get("cycle_id") or "") != identity:
                continue
            if str(value.get("source_commit") or "") != source:
                continue
            if str(value.get("evidence_sha256") or "") != evidence:
                continue
        else:
            if str(value.get("source_commit") or "") != source:
                continue
        reports[provider] = value
    return reports


def _provider_order(preferred: str) -> list[str]:
    preferred = str(preferred or "auto").lower().strip()
    if preferred in PROVIDERS:
        return [preferred] + [x for x in PROVIDER_ORDER if x != preferred]
    return list(PROVIDER_ORDER)


def _run(cmd: list[str], prompt: str, env: dict[str, str], *, stdin: bool = False) -> tuple[int, str, str]:
    try:
        cp = subprocess.run(
            cmd,
            input=prompt if stdin else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=900,
            check=False,
        )
        return cp.returncode, cp.stdout or "", cp.stderr or ""
    except Exception as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"


def _call_provider(provider: str, prompt: str) -> tuple[int, str, str]:
    env = dict(os.environ)
    if provider == "gpt":
        key = str(env.get("OPENAI_API_KEY") or env.get("CODEX_API_KEY") or "").strip()
        if not key:
            return 90, "", "OPENAI_API_KEY missing"
        env["CODEX_API_KEY"] = key
        cmd = ["codex", "--ask-for-approval", "never", "exec", "--sandbox", "read-only", "--ephemeral", "-"]
        return _run(cmd, prompt, env, stdin=True)
    if provider == "gemini":
        if not str(env.get("GEMINI_API_KEY") or "").strip():
            return 90, "", "GEMINI_API_KEY missing"
        model = str(env.get("GEMINI_MASTER_MODEL") or env.get("GEMINI_STRATEGY_MODEL") or "").strip()
        cmd = ["gemini", "--approval-mode=plan", "--skip-trust", "--output-format", "text"]
        if model:
            cmd += ["--model", model]
        cmd += ["-p", prompt]
        return _run(cmd, "", env)
    if provider == "claude":
        if not str(env.get("ANTHROPIC_API_KEY") or "").strip():
            return 90, "", "ANTHROPIC_API_KEY missing"
        model = str(env.get("CLAUDE_MASTER_MODEL") or "sonnet").strip()
        cmd = ["claude", "-p", "--permission-mode", "plan", "--max-turns", "1", "--output-format", "text", "--model", model, prompt]
        return _run(cmd, "", env)
    if provider == "copilot":
        token = str(env.get("COPILOT_GITHUB_TOKEN") or env.get("COPILOT_ASSIGN_TOKEN") or env.get("GITHUB_TOKEN") or "").strip()
        if not token:
            return 90, "", "Copilot token unavailable"
        env["COPILOT_GITHUB_TOKEN"] = token
        # Prompt mode is non-interactive; no tools are auto-approved.
        cmd = ["copilot", "-sp", prompt]
        return _run(cmd, "", env)
    return 91, "", "unsupported provider"


def _extract(text: str) -> dict:
    m = re.search(r"MASTER_DECISION_JSON_BEGIN\s*(\{.*?\})\s*MASTER_DECISION_JSON_END", text, re.S)
    if not m:
        raise ValueError("master JSON markers missing")
    value = json.loads(m.group(1))
    if not isinstance(value, dict):
        raise ValueError("master decision must be an object")
    return value


def _strategy_prompt(identity: str, source: str, evidence: str, reports: dict[str, dict]) -> str:
    payload = json.dumps(reports, indent=2, sort_keys=True)
    return f"""You are the selected MASTER for a resilient multi-agent trading Strategy Lab review.
Cycle: {identity}
Source commit: {source}
Evidence SHA-256: {evidence}
Valid independent reports available: {', '.join(sorted(reports))}

One, two or three other AI agents may be unavailable. Do not wait for them and do not invent their opinions. Use every valid report supplied below. A single valid report is enough to complete adjudication. Missing agents must never stop the trading engine.

The objective is durable NET P&L after all costs. No AI report or master decision may directly trade, change LIVE/capital/wallet/signing settings, weaken sellability/liquidity/simulation protections, or auto-deploy. Strategy implementation is SHADOW-only and draft-only.

Return exactly one object between markers. Use provider names only from gpt, gemini, copilot, claude.
MASTER_DECISION_JSON_BEGIN
{{
  "schema_version":1,
  "cycle_id":"{identity}",
  "source_commit":"{source}",
  "evidence_sha256":"{evidence}",
  "status":"NO_ACTION|DRAFT_SHADOW_CHANGE|HUMAN_REVIEW_REQUIRED",
  "summary":"...",
  "decisions":[{{
    "finding_id":"stable-id",
    "source_proposal_ids":["provider:id"],
    "action":"KEEP|IMPROVE|REWORK|SHADOW_MORE|REPLACE|DORMANT|NEW_SHADOW|ASSET_REQUEST|RESEARCH_MORE",
    "strategy":"...",
    "disposition":"ACCEPT|REJECT|DEFER",
    "reason":"...",
    "confidence":0.0,
    "supporting_agents":["gpt"],
    "risk_class":"LOW|MEDIUM|HIGH|CRITICAL",
    "shadow_only":true,
    "allowed_files":["exact Strategy Lab path"],
    "required_tests":["specific test"]
  }}],
  "implementation_allowed":false,
  "live_auto_deploy":false,
  "draft_pr_only":true
}}
MASTER_DECISION_JSON_END

VALID REPORTS:
{payload}
"""


def _engineering_prompt(source: str, reports: dict[str, dict]) -> str:
    payload = json.dumps(reports, indent=2, sort_keys=True)
    return f"""You are the selected MASTER for a resilient full-repository engineering audit.
Source commit: {source}
Valid independent reports available: {', '.join(sorted(reports))}

One, two or three other AI agents may be unavailable. Do not wait for them and do not invent their opinions. A single valid report is enough to complete adjudication. Missing agents must never stop the trading engine.

Consolidate findings and record ACCEPT, REJECT or DEFER. No AI decision may directly trade or change LIVE/capital/wallet/signing settings. Automated engineering output is draft-PR-only and remains behind deterministic tests and protected-path gates.

Return exactly one object between markers. Use provider names only from gpt, gemini, copilot, claude.
MASTER_DECISION_JSON_BEGIN
{{
  "schema_version":1,
  "source_commit":"{source}",
  "status":"NO_ACTION|DRAFT_FIX|HUMAN_REVIEW_REQUIRED",
  "summary":"...",
  "decisions":[{{
    "finding_id":"stable-id",
    "source_finding_ids":["provider:id"],
    "severity":"P0|P1|P2|P3",
    "title":"...",
    "disposition":"ACCEPT|REJECT|DEFER",
    "reason":"...",
    "confidence":0.0,
    "supporting_agents":["gpt"],
    "deterministic_evidence":false,
    "risk_class":"LOW|MEDIUM|HIGH|CRITICAL",
    "allowed_files":["exact/path.py"],
    "required_tests":["specific test"]
  }}],
  "implementation_allowed":false,
  "live_auto_deploy":false,
  "draft_pr_only":true
}}
MASTER_DECISION_JSON_END

VALID REPORTS:
{payload}
"""


def _validate_base(decision: dict, lane: str, identity: str, source: str, evidence: str) -> None:
    if decision.get("live_auto_deploy") is not False or decision.get("draft_pr_only") is not True:
        raise ValueError("master must be draft-only and live_auto_deploy=false")
    if str(decision.get("source_commit") or "") != source:
        raise ValueError("master source mismatch")
    if lane == "strategy":
        if str(decision.get("cycle_id") or "") != identity:
            raise ValueError("master cycle mismatch")
        if str(decision.get("evidence_sha256") or "") != evidence:
            raise ValueError("master evidence mismatch")
    if not isinstance(decision.get("decisions"), list):
        raise ValueError("master decisions must be a list")


def _gate(decision: dict, lane: str, valid_reports: set[str]) -> dict:
    count = len(valid_reports)
    accepted = 0
    human = False
    rows = []
    for raw in decision.get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        requested = str(row.get("disposition") or "DEFER").upper()
        risk = str(row.get("risk_class") or "HIGH").upper()
        confidence = _num(row.get("confidence"), 0)
        supporters = sorted({str(x).lower() for x in (row.get("supporting_agents") or []) if str(x).lower() in valid_reports})
        files = [_clean_path(x) for x in (row.get("allowed_files") or []) if _clean_path(x)]
        tests = [str(x).strip() for x in (row.get("required_tests") or []) if str(x).strip()]
        eligible = requested == "ACCEPT"
        reasons: list[str] = []

        if lane == "strategy":
            action = str(row.get("action") or "KEEP").upper()
            if action not in {"IMPROVE", "REWORK", "SHADOW_MORE", "NEW_SHADOW"}:
                eligible = False; reasons.append("action is not an auto-code SHADOW action")
            if row.get("shadow_only") is not True:
                eligible = False; human = True; reasons.append("strategy auto-code must be shadow_only=true")
            threshold = 0.95 if count == 1 else 0.85
            if confidence < threshold:
                eligible = False; reasons.append(f"confidence below {threshold:.2f} for {count}-agent evidence")
            if risk not in ({"LOW"} if count == 1 else {"LOW", "MEDIUM"}):
                eligible = False
                if risk in {"HIGH", "CRITICAL"}: human = True
                reasons.append("risk too high for available-agent count")
            if not supporters:
                eligible = False; reasons.append("decision is not supported by an available report")
            if not files or any(not _strategy_path_allowed(p) for p in files):
                eligible = False; human = True; reasons.append("file is outside Strategy Lab SHADOW allow-list")
            if not tests:
                eligible = False; reasons.append("explicit tests required")
        else:
            deterministic = row.get("deterministic_evidence") is True
            threshold = 0.95 if count == 1 else 0.85
            if confidence < threshold:
                eligible = False; reasons.append(f"confidence below {threshold:.2f} for {count}-agent evidence")
            if count == 1 and not deterministic:
                eligible = False; reasons.append("single-agent engineering auto-fix requires deterministic evidence")
            if risk not in {"LOW", "MEDIUM"}:
                eligible = False; human = True; reasons.append("HIGH/CRITICAL engineering change requires human review")
            if str(row.get("severity") or "P3").upper() == "P0":
                eligible = False; human = True; reasons.append("P0 requires human review")
            if not supporters:
                eligible = False; reasons.append("decision is not supported by an available report")
            if not files or any(not _engineering_path_allowed(p) for p in files):
                eligible = False; human = True; reasons.append("protected or missing file scope")
            if not tests:
                eligible = False; reasons.append("explicit tests required")

        row["supporting_agents"] = supporters
        row["allowed_files"] = files
        row["policy_eligible"] = bool(eligible)
        row["policy_reasons"] = reasons or ["resilient master policy requirements satisfied"]
        rows.append(row)
        if eligible:
            accepted += 1

    out = dict(decision)
    out["decisions"] = rows
    out["valid_agents"] = sorted(valid_reports)
    out["valid_agent_count"] = count
    out["failed_agent_count"] = max(0, 4 - count)
    out["resilient_cycle_continued"] = count >= 1
    out["policy_accepted_count"] = accepted
    out["implementation_allowed"] = accepted > 0 and not human
    out["live_auto_deploy"] = False
    out["draft_pr_only"] = True
    out["policy"] = {
        "minimum_valid_reports_to_continue": 1,
        "single_agent_strategy_confidence": 0.95,
        "single_agent_strategy_risk": "LOW_ONLY",
        "single_agent_engineering_requires_deterministic_evidence": True,
        "live_trading_depends_on_ai_health": False,
        "auto_merge": False,
        "auto_deploy": False,
    }
    if human:
        out["status"] = "HUMAN_REVIEW_REQUIRED"
        out["implementation_allowed"] = False
    elif accepted:
        out["status"] = "DRAFT_SHADOW_CHANGE" if lane == "strategy" else "DRAFT_FIX"
    elif str(out.get("status") or "").startswith("DRAFT"):
        out["status"] = "NO_ACTION"
    return out


def _fallback(lane: str, identity: str, source: str, evidence: str, reports: dict[str, dict], attempts: list[dict]) -> dict:
    out = {
        "schema_version": 1,
        "source_commit": source,
        "status": "HUMAN_REVIEW_REQUIRED",
        "summary": "All selectable master providers failed. The cycle still completed deterministically from the available reports, but no AI synthesis was trusted.",
        "decisions": [],
        "implementation_allowed": False,
        "live_auto_deploy": False,
        "draft_pr_only": True,
        "valid_agents": sorted(reports),
        "valid_agent_count": len(reports),
        "failed_agent_count": max(0, 4-len(reports)),
        "resilient_cycle_continued": bool(reports),
        "actual_master": "deterministic_fallback",
        "master_attempts": attempts,
    }
    if lane == "strategy":
        out["cycle_id"] = identity
        out["evidence_sha256"] = evidence
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", required=True, choices=("strategy", "engineering"))
    ap.add_argument("--identity", required=True)
    ap.add_argument("--source-commit", required=True)
    ap.add_argument("--evidence-sha", default="")
    ap.add_argument("--reports-dir", required=True)
    ap.add_argument("--preferred-master", default="auto")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    folder = Path(args.reports_dir)
    reports = _load_reports(folder, args.lane, args.identity, args.source_commit, args.evidence_sha)
    if not reports:
        print("no valid AI report exists; cycle must retry", file=sys.stderr)
        return 20

    prompt = _strategy_prompt(args.identity, args.source_commit, args.evidence_sha, reports) if args.lane == "strategy" else _engineering_prompt(args.source_commit, reports)
    attempts: list[dict] = []
    decision = None
    actual = ""
    for provider in _provider_order(args.preferred_master):
        rc, stdout, stderr = _call_provider(provider, prompt)
        reason = (stderr or stdout)[-1200:].replace("\x00", " ")
        attempt = {"provider": provider, "success": False, "return_code": rc, "reason": reason[:1200]}
        if rc == 0:
            try:
                candidate = _extract(stdout)
                _validate_base(candidate, args.lane, args.identity, args.source_commit, args.evidence_sha)
                decision = candidate
                actual = provider
                attempt["success"] = True
                attempt["reason"] = "master decision validated"
                attempts.append(attempt)
                break
            except Exception as exc:
                attempt["reason"] = f"invalid master output: {type(exc).__name__}: {exc}"
        attempts.append(attempt)

    if decision is None:
        gated = _fallback(args.lane, args.identity, args.source_commit, args.evidence_sha, reports, attempts)
    else:
        gated = _gate(decision, args.lane, set(reports))
        gated["preferred_master"] = str(args.preferred_master or "auto").lower()
        gated["actual_master"] = actual
        gated["master_attempts"] = attempts
    gated["unavailable_agents"] = [x for x in PROVIDERS if x not in reports]
    Path(args.output).write_text(json.dumps(gated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("actual_master=" + str(gated.get("actual_master") or "deterministic_fallback"))
    print("valid_agent_count=" + str(gated.get("valid_agent_count") or 0))
    print("implementation_allowed=" + ("true" if gated.get("implementation_allowed") else "false"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
