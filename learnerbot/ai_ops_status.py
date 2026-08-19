from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .user_registry import all_users

AI_REVIEW_REF = "refs/remotes/origin/ai-reviews"


def master_chat_ids(csv_dir: Path) -> list[str]:
    """Return ACTIVE MASTER Telegram IDs from the existing user registry."""
    out: list[str] = []
    for row in all_users(Path(csv_dir)):
        tid = str(row.get("telegram_id") or "").strip()
        if not tid or not tid.lstrip("-").isdigit():
            continue
        if str(row.get("role") or "USER").upper() != "MASTER":
            continue
        if str(row.get("status") or "").upper() != "ACTIVE":
            continue
        if tid not in out:
            out.append(tid)
    return out[:5]


def fetch_ai_reviews(repo_root: Path, *, timeout: int = 25) -> tuple[bool, str]:
    """Refresh only the sanitised ai-reviews remote ref; never switch the worktree."""
    root = Path(repo_root)
    try:
        p = subprocess.run(
            ["git", "-C", str(root), "fetch", "--quiet", "origin", f"ai-reviews:{AI_REVIEW_REF}"],
            text=True,
            capture_output=True,
            timeout=max(5, int(timeout)),
            check=False,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if p.returncode:
        detail = (p.stderr or p.stdout or "git fetch failed").strip()
        return False, detail[:400]
    return True, "OK"


def _show(repo_root: Path, path: str) -> str | None:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{AI_REVIEW_REF}:{path}"],
            text=True,
            capture_output=True,
            timeout=12,
            check=False,
        )
    except Exception:
        return None
    if p.returncode:
        return None
    return p.stdout


def read_text(repo_root: Path, path: str) -> str | None:
    raw = _show(repo_root, path)
    return raw.strip() if raw is not None else None


def read_json(repo_root: Path, path: str) -> dict | None:
    raw = _show(repo_root, path)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _agent_status(report: dict | None) -> str:
    if not report:
        return "WAITING"
    status = str(report.get("status") or "UNKNOWN").upper()
    if status == "INCOMPLETE":
        return "INCOMPLETE"
    if status in {"CLEAN", "ISSUES_FOUND"}:
        return "DONE"
    return status


def engineering_status(repo_root: Path) -> dict:
    source = read_text(repo_root, "weekly/latest_source_commit.txt") or ""
    kickoff = read_text(repo_root, "weekly/latest_kickoff_utc.txt") or ""
    completed_source = read_text(repo_root, "weekly/latest_completed_source_commit.txt") or ""
    completed_utc = read_text(repo_root, "weekly/latest_completed_utc.txt") or ""
    if not source:
        return {
            "available": False,
            "phase": "ENGINEERING",
            "source_commit": "",
            "gpt": "WAITING",
            "gemini": "WAITING",
            "copilot": "WAITING",
            "three_agent_reports_complete": False,
            "master_decision_available": False,
        }

    run_root = f"weekly/runs/{source}"
    gpt = read_json(repo_root, f"{run_root}/gpt.json")
    gemini = read_json(repo_root, f"{run_root}/gemini.json")
    copilot = read_json(repo_root, f"{run_root}/copilot.json")
    completion = read_json(repo_root, f"{run_root}/completion.json") or {}
    master = read_json(repo_root, f"{run_root}/master_decision.json")
    action = read_json(repo_root, f"{run_root}/action_status.json") or {}

    done = bool(
        completed_source == source
        and completion.get("three_agent_reports_complete") is True
        and _agent_status(gpt) == "DONE"
        and _agent_status(gemini) == "DONE"
        and _agent_status(copilot) == "DONE"
    )
    return {
        "available": True,
        "phase": "ENGINEERING",
        "source_commit": source,
        "kickoff_utc": kickoff,
        "completed_utc": completed_utc if completed_source == source else "",
        "gpt": _agent_status(gpt),
        "gemini": _agent_status(gemini),
        "copilot": _agent_status(copilot),
        "three_agent_reports_complete": done,
        "master_decision_available": bool(master),
        "master_status": str((master or {}).get("status") or "WAITING"),
        "master_summary": str((master or {}).get("summary") or "")[:900],
        "decision_counts": decision_counts(master),
        "policy_accepted_count": int((master or {}).get("policy_accepted_count") or 0),
        "implementation_allowed": bool((master or {}).get("implementation_allowed")),
        "corrective_pr_url": str(action.get("corrective_pr_url") or ""),
        "corrective_branch": str(action.get("corrective_branch") or ""),
        "action_state": str(action.get("state") or ("MASTER_DECIDED" if master else "WAITING")),
    }


def decision_counts(master: dict | None) -> dict[str, int]:
    out = {"ACCEPT": 0, "REJECT": 0, "DEFER": 0}
    for row in (master or {}).get("decisions") or []:
        value = str((row or {}).get("disposition") or "").upper()
        if value in out:
            out[value] += 1
    return out


def decision_rows(master: dict | None, disposition: str | None = None, *, limit: int = 8) -> list[dict]:
    wanted = str(disposition or "").upper().strip()
    rows = []
    for raw in (master or {}).get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        d = str(raw.get("disposition") or "").upper()
        if wanted and d != wanted:
            continue
        rows.append({
            "id": str(raw.get("finding_id") or "")[:80],
            "severity": str(raw.get("severity") or ""),
            "title": str(raw.get("title") or "")[:240],
            "disposition": d,
            "reason": str(raw.get("reason") or "")[:700],
            "confidence": raw.get("confidence"),
            "policy_eligible": bool(raw.get("policy_eligible")),
            "policy_reasons": [str(x)[:220] for x in (raw.get("policy_reasons") or [])[:4]],
        })
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def latest_master_decision(repo_root: Path) -> dict | None:
    return read_json(repo_root, "weekly/latest_master_decision.json")


def strategy_status(repo_root: Path) -> dict:
    # The three-agent strategy pipeline publishes these paths. Until its first run,
    # commands report WAITING rather than re-labelling the hourly Gemini-only review.
    status = read_json(repo_root, "strategy/latest_status.json")
    master = read_json(repo_root, "strategy/latest_master_decision.json")
    if not status:
        return {
            "available": False,
            "phase": "STRATEGY",
            "gpt": "WAITING",
            "gemini": "WAITING",
            "copilot": "WAITING",
            "three_agent_reports_complete": False,
            "master_decision_available": False,
            "state": "WAITING_FOR_THREE_AGENT_STRATEGY_CYCLE",
        }
    out = dict(status)
    out.setdefault("phase", "STRATEGY")
    out["master_decision_available"] = bool(master)
    out["decision_counts"] = decision_counts(master)
    return out


def notification_state(repo_root: Path) -> dict:
    engineering = engineering_status(repo_root)
    strategy = strategy_status(repo_root)
    return {"engineering": engineering, "strategy": strategy}


def state_hash(state: dict) -> str:
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_sent_state(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_sent_state(path: Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def transition_messages(previous: dict, current: dict) -> list[str]:
    """Return only meaningful operator transitions, not every branch mutation."""
    messages: list[str] = []
    pe = (previous or {}).get("engineering") or {}
    ce = (current or {}).get("engineering") or {}
    if ce.get("available") and ce.get("source_commit") != pe.get("source_commit"):
        messages.append(
            "🧪 AI ENGINEERING AUDIT STARTED\n"
            f"Source: {str(ce.get('source_commit') or '')[:12]}\n"
            "GPT + Gemini auditing independently; Copilot report is requested separately."
        )
    if ce.get("source_commit") and ce.get("source_commit") == pe.get("source_commit"):
        changed_agents = [
            name.upper() for name in ("gpt", "gemini", "copilot")
            if ce.get(name) != pe.get(name) and ce.get(name) in {"DONE", "INCOMPLETE"}
        ]
        if changed_agents:
            messages.append(
                "📋 AI ENGINEERING REPORT UPDATE\n"
                + "\n".join(f"{name}: {ce.get(name.lower())}" for name in changed_agents)
                + f"\nAll 3 complete: {'YES' if ce.get('three_agent_reports_complete') else 'NO'}"
            )
    if ce.get("three_agent_reports_complete") and not pe.get("three_agent_reports_complete"):
        messages.append("✅ THREE ENGINEERING AGENTS COMPLETE\nGPT ✅  Gemini ✅  Copilot ✅\nGPT master adjudication is available or starting.")
    if ce.get("master_decision_available") and (
        not pe.get("master_decision_available") or ce.get("decision_counts") != pe.get("decision_counts")
    ):
        c = ce.get("decision_counts") or {}
        messages.append(
            "🧠 GPT MASTER ENGINEERING DECISION\n"
            f"Status: {ce.get('master_status')}\n"
            f"ACCEPT {c.get('ACCEPT', 0)} | REJECT {c.get('REJECT', 0)} | DEFER {c.get('DEFER', 0)}\n"
            f"Policy-approved fixes: {ce.get('policy_accepted_count', 0)}\n"
            "Use /aidecision for reasons and policy overrides."
        )
    if ce.get("corrective_pr_url") and ce.get("corrective_pr_url") != pe.get("corrective_pr_url"):
        messages.append(
            "🛠 GPT CORRECTIVE ACTION READY\n"
            f"Draft PR: {ce.get('corrective_pr_url')}\n"
            "Tests passed before the draft was created. No automatic merge/deployment was authorised."
        )

    ps = (previous or {}).get("strategy") or {}
    cs = (current or {}).get("strategy") or {}
    if cs.get("available") and cs.get("cycle_id") != ps.get("cycle_id"):
        messages.append("🔬 THREE-AGENT STRATEGY REVIEW STARTED\nGPT + Gemini + Copilot are reviewing the same evidence/base.")
    if cs.get("three_agent_reports_complete") and not ps.get("three_agent_reports_complete"):
        messages.append("✅ THREE STRATEGY AGENTS COMPLETE\nGPT ✅  Gemini ✅  Copilot ✅\nGPT strategy adjudication is available or starting.")
    if cs.get("master_decision_available") and not ps.get("master_decision_available"):
        c = cs.get("decision_counts") or {}
        messages.append(
            "🧠 GPT MASTER STRATEGY DECISION\n"
            f"ACCEPT {c.get('ACCEPT', 0)} | REJECT {c.get('REJECT', 0)} | DEFER {c.get('DEFER', 0)}\n"
            "Use /aistrategy for the current strategy-cycle status."
        )
    if cs.get("change_pr_url") and cs.get("change_pr_url") != ps.get("change_pr_url"):
        messages.append(
            "🧬 STRATEGY CHANGE DRAFT READY\n"
            f"Draft PR: {cs.get('change_pr_url')}\n"
            "Strategy changes remain gated/shadow-first; no automatic live deployment was authorised."
        )
    return messages


def snapshot_for_display(repo_root: Path) -> dict:
    ok, detail = fetch_ai_reviews(repo_root)
    state = notification_state(repo_root)
    state["fetch_ok"] = ok
    state["fetch_detail"] = detail
    state["checked_epoch"] = int(time.time())
    return state
