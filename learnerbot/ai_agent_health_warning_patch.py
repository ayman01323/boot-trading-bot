from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import cli as _cli
from . import telegram as _tg
from .ai_ops_status import fetch_ai_reviews, master_chat_ids, read_json, read_text

_PREV_APP = _cli._app
_THREAD_LOCK = threading.Lock()
_THREAD_STARTED = False
CHECK_SECONDS = 60
WARNING_SECONDS = 30 * 60
WAIT_GRACE_SECONDS = 30 * 60


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _state_path(app) -> Path:
    return Path(app.data_dir) / ".ai_agent_health_warning_state.json"


def _clean_reason(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip().replace("\x00", "")
    text = re.sub(r"\b(sk|sess)-[A-Za-z0-9_-]{8,}\b", "<redacted>", text)
    text = re.sub(r"\bAIza[A-Za-z0-9_-]{20,}\b", "<redacted>", text)
    text = re.sub(r"\s+", " ", text)
    return text[:limit] or "no diagnostic reason was published"


def _report_reason(report: dict | None, *, strategy: bool = False) -> str:
    if not isinstance(report, dict):
        return "report file is not available"
    if strategy:
        rows = report.get("evidence_gaps") or []
    else:
        rows = report.get("refusals_or_limits") or []
    if isinstance(rows, list) and rows:
        return _clean_reason(rows[0])
    return _clean_reason(report.get("summary") or "report is incomplete")


def _age_from_iso(value: str, now: int) -> int:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, now - int(dt.timestamp()))
    except Exception:
        return 10**9


def _strategy_cycle_age(cycle_id: str, now: int) -> int:
    """Hourly strategy cycles normally start at minute 17 of their encoded UTC hour."""
    try:
        parts = str(cycle_id or "").split("-")
        hour_key = parts[-2]
        dt = datetime.strptime(hour_key, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        started = int(dt.timestamp()) + 17 * 60
        return max(0, now - started)
    except Exception:
        return 10**9


def _agent(report: dict | None, *, age: int, strategy: bool, waiting_reason: str) -> dict[str, str]:
    if isinstance(report, dict):
        status = str(report.get("status") or "").upper()
        if status and status != "INCOMPLETE":
            return {"state": "WORKING", "reason": status}
        if status == "INCOMPLETE":
            return {"state": "NOT_WORKING", "reason": _report_reason(report, strategy=strategy)}
    if age >= WAIT_GRACE_SECONDS:
        return {"state": "NOT_WORKING", "reason": _clean_reason(waiting_reason)}
    return {"state": "WAITING", "reason": _clean_reason(waiting_reason)}


def _engineering_health(root: Path, now: int) -> dict:
    source = read_text(root, "weekly/latest_source_commit.txt") or ""
    kickoff = read_text(root, "weekly/latest_kickoff_utc.txt") or ""
    if not source:
        return {"available": False, "agents": {}, "valid_count": 0, "master": "WAITING"}
    age = _age_from_iso(kickoff, now)
    run = f"weekly/runs/{source}"
    gpt = read_json(root, f"{run}/gpt.json")
    gemini = read_json(root, f"{run}/gemini.json")
    copilot = read_json(root, f"{run}/copilot.json")
    availability = read_json(root, f"{run}/agent_availability.json") or {}
    reconciled = read_json(root, f"{run}/copilot_assignment_reconciled.json") or {}
    original = read_json(root, f"{run}/copilot_assignment.json") or {}
    valid = {str(x).lower() for x in availability.get("valid_agents") or []}
    reasons = availability.get("reasons") if isinstance(availability.get("reasons"), dict) else {}

    agents = {
        "gpt": _agent(gpt, age=age, strategy=False, waiting_reason=str(reasons.get("gpt") or "GPT engineering report has not completed")),
        "gemini": _agent(gemini, age=age, strategy=False, waiting_reason=str(reasons.get("gemini") or "Gemini engineering report has not completed")),
    }
    if "copilot" in valid:
        agents["copilot"] = {"state": "WORKING", "reason": "valid Copilot report reconciled"}
    else:
        assignment_reason = (
            reasons.get("copilot")
            or reconciled.get("reason")
            or reconciled.get("assignment_state")
            or original.get("stage")
            or original.get("assignment_outcome")
            or "Copilot engineering report has not completed"
        )
        agents["copilot"] = _agent(copilot, age=age, strategy=False, waiting_reason=str(assignment_reason))

    valid_count = sum(1 for row in agents.values() if row.get("state") == "WORKING")
    completion = read_json(root, f"{run}/completion.json") or {}
    if isinstance(completion.get("valid_agent_count"), int):
        valid_count = max(valid_count, int(completion.get("valid_agent_count") or 0))
    master = "CONTINUING" if valid_count else "RETRYING"
    return {
        "available": True,
        "cycle": source[:12],
        "source_commit": source,
        "age_seconds": age,
        "agents": agents,
        "valid_count": min(3, valid_count),
        "master": master,
    }


def _strategy_health(root: Path, now: int) -> dict:
    status = read_json(root, "strategy/latest_status.json") or {}
    cycle = str(status.get("cycle_id") or "")
    if not cycle:
        return {"available": False, "agents": {}, "valid_count": 0, "master": "WAITING"}
    age = _strategy_cycle_age(cycle, now)
    run = f"strategy/runs/{cycle}"
    context = read_json(root, f"{run}/context.json") or {}
    reports = {
        "gpt": read_json(root, f"{run}/gpt.json"),
        "gemini": read_json(root, f"{run}/gemini.json"),
        "copilot": read_json(root, f"{run}/copilot.json"),
    }
    agents: dict[str, dict[str, str]] = {}
    for name in ("gpt", "gemini"):
        agents[name] = _agent(
            reports[name],
            age=age,
            strategy=True,
            waiting_reason=f"{name.upper()} strategy report has not completed",
        )

    copilot_state = str(status.get("copilot") or context.get("copilot_assignment_state") or "WAITING").upper()
    if isinstance(reports["copilot"], dict) and str(reports["copilot"].get("status") or "").upper() != "INCOMPLETE":
        agents["copilot"] = {"state": "WORKING", "reason": str(reports["copilot"].get("status") or "valid report")}
    elif copilot_state in {"BLOCKED_AUTH", "WAITING_ASSIGNMENT"}:
        agents["copilot"] = {
            "state": "NOT_WORKING",
            "reason": "Copilot assignment authentication failed" if copilot_state == "BLOCKED_AUTH" else "Copilot assignment is still pending",
        }
    else:
        agents["copilot"] = _agent(
            reports["copilot"],
            age=age,
            strategy=True,
            waiting_reason=f"Copilot state {copilot_state}; report has not been reconciled",
        )
    valid_count = sum(1 for row in agents.values() if row.get("state") == "WORKING")
    master = "DECIDED" if status.get("master_decision_available") else ("WAITING_FOR_RECONCILIATION" if valid_count else "RETRYING")
    return {
        "available": True,
        "cycle": cycle,
        "age_seconds": age,
        "agents": agents,
        "valid_count": valid_count,
        "master": master,
    }


def build_health_snapshot(repo_root: Path, *, now: int | None = None) -> dict:
    current = int(now or time.time())
    root = Path(repo_root)
    return {
        "engineering": _engineering_health(root, current),
        "strategy": _strategy_health(root, current),
        "checked_epoch": current,
    }


def unhealthy_rows(snapshot: dict) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for lane in ("engineering", "strategy"):
        part = (snapshot or {}).get(lane) or {}
        for name, detail in (part.get("agents") or {}).items():
            if str((detail or {}).get("state") or "") == "NOT_WORKING":
                rows.append((lane, str(name), _clean_reason((detail or {}).get("reason"))))
    return rows


def health_signature(snapshot: dict) -> str:
    payload = unhealthy_rows(snapshot)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _icon(state: str) -> str:
    return {"WORKING": "✅", "NOT_WORKING": "⚠️", "WAITING": "⏳"}.get(str(state or ""), "•")


def warning_message(snapshot: dict) -> str:
    lines = ["🚨 AI AGENT HEALTH WARNING"]
    for lane, label in (("engineering", "ENGINEERING"), ("strategy", "STRATEGY")):
        part = (snapshot or {}).get(lane) or {}
        if not part.get("available"):
            continue
        lines += ["", f"{label} {str(part.get('cycle') or '')[:40]}"]
        for name in ("gpt", "gemini", "copilot"):
            detail = (part.get("agents") or {}).get(name) or {"state": "WAITING", "reason": "not available"}
            state = str(detail.get("state") or "WAITING")
            text = f"{name.upper()}: {_icon(state)} {state}"
            if state != "WORKING":
                text += f" — {_clean_reason(detail.get('reason'), 320)}"
            lines.append(text)
        if lane == "engineering":
            n = int(part.get("valid_count") or 0)
            lines.append(f"Master: {'continuing with '+str(n)+'/3 valid report(s)' if n else 'retrying until at least one valid report exists'}.")
        else:
            lines.append(f"Master: {str(part.get('master') or 'WAITING').replace('_', ' ')}.")
    lines += ["", "This warning repeats every 30 minutes while any agent remains unhealthy."]
    return "\n".join(lines)


def recovery_message(snapshot: dict) -> str:
    return (
        "✅ AI AGENT HEALTH RECOVERED\n"
        "No AI agent is currently marked NOT_WORKING in the latest monitored engineering/strategy cycles.\n"
        "Normal report reconciliation continues."
    )


def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _watch_loop(app) -> None:
    time.sleep(20)
    state_path = _state_path(app)
    sent = _load_state(state_path)
    while True:
        try:
            ok, detail = fetch_ai_reviews(_repo_root(), timeout=20)
            if not ok:
                print(f"[ai-agent-health] ai-reviews fetch failed: {detail}")
                time.sleep(CHECK_SECONDS)
                continue
            now = int(time.time())
            snapshot = build_health_snapshot(_repo_root(), now=now)
            unhealthy = unhealthy_rows(snapshot)
            signature = health_signature(snapshot)
            last_signature = str(sent.get("last_signature") or "")
            last_sent = int(sent.get("last_sent_epoch") or 0)
            had_unhealthy = bool(sent.get("had_unhealthy"))
            masters = master_chat_ids(Path(app.csv_dir))
            token = str(getattr(app, "telegram_bot_token", "") or "").strip()

            if unhealthy:
                due = signature != last_signature or now - last_sent >= WARNING_SECONDS
                if due and token and masters:
                    _tg.send_to_chats(token, masters, warning_message(snapshot), disable_notification=False)
                    sent = {
                        "had_unhealthy": True,
                        "last_signature": signature,
                        "last_sent_epoch": now,
                        "unhealthy_count": len(unhealthy),
                    }
                    _save_state(state_path, sent)
            elif had_unhealthy:
                if token and masters:
                    _tg.send_to_chats(token, masters, recovery_message(snapshot), disable_notification=False)
                sent = {
                    "had_unhealthy": False,
                    "last_signature": signature,
                    "last_sent_epoch": now,
                    "unhealthy_count": 0,
                }
                _save_state(state_path, sent)
        except Exception as exc:
            print(f"[ai-agent-health] {type(exc).__name__}: {exc}")
        time.sleep(CHECK_SECONDS)


def _start(app) -> None:
    global _THREAD_STARTED
    with _THREAD_LOCK:
        if _THREAD_STARTED or not getattr(app, "telegram_bot_token", ""):
            return
        thread = threading.Thread(target=_watch_loop, args=(app,), name="ai-agent-health-warning", daemon=True)
        thread.start()
        _THREAD_STARTED = True
        print("[ai-agent-health] started check=60s warning_repeat=1800s master-role-dynamic=true")


def _app_with_agent_health():
    app = _PREV_APP()
    try:
        _start(app)
    except Exception as exc:
        print(f"[ai-agent-health-start] {type(exc).__name__}: {exc}")
    return app


_cli._app = _app_with_agent_health
