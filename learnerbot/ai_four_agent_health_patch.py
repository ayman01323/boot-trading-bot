from __future__ import annotations

import html
import time
from pathlib import Path

from . import ai_agent_health_warning_patch as _health
from . import ai_agent_health_master_reconcile_patch as _legacy_reconcile  # noqa: F401
from . import telegram_ai_ops_patch as _ai

PROVIDERS = ("gpt", "claude", "gemini", "copilot")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _report_state(report: dict | None, *, age: int, lane: str, provider: str, reason: str = "") -> dict[str, str]:
    if isinstance(report, dict):
        status = str(report.get("status") or "").upper()
        if status and status not in {"INCOMPLETE", "FAILED"}:
            return {"state": "WORKING", "reason": status}
        if status in {"INCOMPLETE", "FAILED"}:
            return {
                "state": "NOT_WORKING",
                "reason": _health._report_reason(report, strategy=(lane == "strategy")),
            }
    if age >= _health.WAIT_GRACE_SECONDS:
        return {"state": "NOT_WORKING", "reason": _health._clean_reason(reason or f"{provider} report did not complete")}
    return {"state": "WAITING", "reason": _health._clean_reason(reason or f"waiting for {provider} report")}


def _selected_master(root: Path, run: str) -> dict:
    completion = _health.read_json(root, f"{run}/selected_master_completion.json") or {}
    decision = _health.read_json(root, f"{run}/selected_master_decision.json") or _health.read_json(root, f"{run}/master_decision.json") or {}
    actual = str(completion.get("actual_master") or decision.get("actual_master") or "")
    preferred = str(completion.get("preferred_master") or decision.get("preferred_master") or "auto")
    attempts = completion.get("master_attempts") or decision.get("master_attempts") or []
    failed = []
    if isinstance(attempts, list):
        for row in attempts:
            if isinstance(row, dict) and not row.get("success"):
                name = str(row.get("provider") or "").lower()
                if name in PROVIDERS and name not in failed:
                    failed.append(name)
    return {
        "available": bool(completion.get("master_decision_available") or decision.get("status")),
        "actual": actual,
        "preferred": preferred,
        "failed_attempts": failed,
        "valid_count": int(completion.get("valid_agent_count") or decision.get("valid_agent_count") or 0),
    }


def _engineering_health(root: Path, now: int) -> dict:
    source = _health.read_text(root, "weekly/latest_source_commit.txt") or ""
    kickoff = _health.read_text(root, "weekly/latest_kickoff_utc.txt") or ""
    if not source:
        return {"available": False, "agents": {}, "valid_count": 0, "master": "WAITING"}
    age = _health._age_from_iso(kickoff, now)
    run = f"weekly/runs/{source}"
    availability = _health.read_json(root, f"{run}/agent_availability.json") or {}
    reasons = availability.get("reasons") if isinstance(availability.get("reasons"), dict) else {}
    reports = {name: _health.read_json(root, f"{run}/{name}.json") for name in PROVIDERS}
    agents = {
        name: _report_state(
            reports[name], age=age, lane="engineering", provider=name,
            reason=str(reasons.get(name) or f"{name.upper()} engineering report has not completed"),
        )
        for name in PROVIDERS
    }
    selected = _selected_master(root, run)
    valid_count = sum(1 for row in agents.values() if row.get("state") == "WORKING")
    return {
        "available": True,
        "cycle": source[:12],
        "source_commit": source,
        "age_seconds": age,
        "agents": agents,
        "valid_count": valid_count,
        "master": ("DECIDED:" + selected["actual"].upper()) if selected["available"] and selected["actual"] else ("CONTINUING" if valid_count else "RETRYING"),
        "selected_master": selected,
    }


def _strategy_health(root: Path, now: int) -> dict:
    status = _health.read_json(root, "strategy/latest_status.json") or {}
    cycle = str(_health.read_text(root, "strategy/latest_cycle_id.txt") or status.get("cycle_id") or "").strip()
    if not cycle:
        return {"available": False, "agents": {}, "valid_count": 0, "master": "WAITING"}
    age = _health._strategy_cycle_age(cycle, now)
    run = f"strategy/runs/{cycle}"
    reports = {name: _health.read_json(root, f"{run}/{name}.json") for name in PROVIDERS}
    agents = {}
    for name in PROVIDERS:
        reason = f"{name.upper()} strategy report has not completed"
        if name == "copilot":
            copilot_state = str(status.get("copilot") or "").upper()
            if copilot_state == "BLOCKED_AUTH":
                reason = "Copilot assignment/authentication is not working"
        agents[name] = _report_state(reports[name], age=age, lane="strategy", provider=name, reason=reason)
    selected = _selected_master(root, run)
    valid_count = sum(1 for row in agents.values() if row.get("state") == "WORKING")
    return {
        "available": True,
        "cycle": cycle,
        "age_seconds": age,
        "agents": agents,
        "valid_count": valid_count,
        "master": ("DECIDED:" + selected["actual"].upper()) if selected["available"] and selected["actual"] else ("WAITING_FOR_RECONCILIATION" if valid_count else "RETRYING"),
        "selected_master": selected,
    }


def _icon(state: str) -> str:
    return {"WORKING": "✅", "NOT_WORKING": "⚠️", "WAITING": "⏳"}.get(str(state or ""), "•")


def warning_message(snapshot: dict) -> str:
    lines = ["🚨 AI AGENT HEALTH WARNING"]
    for lane, label in (("engineering", "ENGINEERING"), ("strategy", "STRATEGY")):
        part = (snapshot or {}).get(lane) or {}
        if not part.get("available"):
            continue
        lines += ["", f"{label} {str(part.get('cycle') or '')[:40]}"]
        for name in PROVIDERS:
            detail = (part.get("agents") or {}).get(name) or {"state": "WAITING", "reason": "not available"}
            state = str(detail.get("state") or "WAITING")
            text = f"{name.upper()}: {_icon(state)} {state}"
            if state != "WORKING":
                text += f" — {_health._clean_reason(detail.get('reason'), 300)}"
            lines.append(text)
        n = int(part.get("valid_count") or 0)
        selected = part.get("selected_master") or {}
        preferred = str(selected.get("preferred") or "auto").upper()
        actual = str(selected.get("actual") or "").upper()
        if actual:
            lines.append(f"MASTER: ✅ {actual} (preferred {preferred}); cycle continued with {n}/4 valid report(s).")
        elif n:
            lines.append(f"MASTER: fallback reconciliation continues with {n}/4 valid report(s).")
        else:
            lines.append("MASTER: no valid report yet; review lane retries, but LIVE trading is not stopped by AI health.")
    lines += [
        "",
        "Fallback order: selected MASTER → GPT → Claude → Gemini → other available agent.",
        "AI failure never disables the trading engine. Existing wallet/signing/simulation/liquidity/capital/LIVE safety gates remain authoritative.",
        "This warning repeats every 30 minutes while any agent remains unhealthy.",
    ]
    return "\n".join(lines)


def _ops_text(lane: str, state: dict) -> str:
    now = int(time.time())
    health = _engineering_health(_repo_root(), now) if lane == "engineering" else _strategy_health(_repo_root(), now)
    title = "🧪 AI ENGINEERING AUDIT" if lane == "engineering" else "🔬 AI STRATEGY REVIEW"
    if not health.get("available"):
        return f"<b>{title}</b>\n\nNo published {lane} cycle yet."
    lines = [f"<b>{title}</b>", "", f"Cycle: <code>{html.escape(str(health.get('cycle') or ''))}</code>"]
    for name in PROVIDERS:
        detail = (health.get("agents") or {}).get(name) or {}
        s = str(detail.get("state") or "WAITING")
        lines.append(f"{name.upper()}: {_icon(s)} <b>{html.escape(s)}</b>")
        if s == "NOT_WORKING":
            lines.append(f"↳ {html.escape(_health._clean_reason(detail.get('reason'), 240))}")
    selected = health.get("selected_master") or {}
    lines += [
        "",
        f"Preferred MASTER: <b>{html.escape(str(selected.get('preferred') or 'auto').upper())}</b>",
        f"Actual MASTER: <b>{html.escape(str(selected.get('actual') or 'WAITING').upper())}</b>",
        f"Valid reports: <b>{int(health.get('valid_count') or 0)}/4</b>",
        "",
        "<i>One valid report is sufficient for the review cycle to continue. AI health does not turn LIVE trading off.</i>",
    ]
    return "\n".join(lines)


def _engineering_text(state: dict) -> str:
    return _ops_text("engineering", state)


def _strategy_text(state: dict) -> str:
    return _ops_text("strategy", state)


def install() -> None:
    _health._engineering_health = _engineering_health
    _health._strategy_health = _strategy_health
    _health.warning_message = warning_message
    _ai._engineering_text = _engineering_text
    _ai._strategy_text = _strategy_text


install()
