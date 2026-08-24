from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

MIN_INTERVAL_HOURS = 4
MAX_INTERVAL_HOURS = 24 * 30

REPORTS = {
    "trade": {
        "label": "Trade Strategy Economic Monitor",
        "default_hours": 4,
        "mode": "trade-strategy-economics",
        "description": "Value-weighted trade economics: net P&L after costs, profit factor and winning value versus losing value.",
    },
    "engineering": {
        "label": "Engineering Monitor",
        "default_hours": 48,
        "mode": "observe-engineering",
        "description": "Deterministic engineering/infrastructure report; findings are queued to Strategy Factory Review.",
    },
    "strategy": {
        "label": "Strategy Monitor",
        "default_hours": 48,
        "mode": "observe-strategy",
        "description": "Full Strategy Lab portfolio review; findings are queued to Strategy Factory Review.",
    },
    "factory": {
        "label": "Strategy Factory Review",
        "default_hours": 6,
        "mode": "factory-review",
        "description": "Central action/adjudication hub. AI is called only when queued packages exist.",
    },
    "engineering_ai": {
        "label": "Rotating AI Engineering Review",
        "default_hours": 48,
        "mode": "engineering-rotation",
        "description": "One rotating non-GPT agent reviews engineering evidence; GPT validates and queues supported problems.",
    },
    "seven_agent": {
        "label": "Seven-Agent Strategy/Factory/Engineering Review",
        "default_hours": 168,
        "mode": "weekly-joint",
        "description": "All available agents challenge the whole operating model; GPT synthesises and queues findings to Factory.",
    },
}

ALIASES = {
    "trade_strategy": "trade",
    "economics": "trade",
    "pnl": "trade",
    "eng": "engineering",
    "engineering_monitor": "engineering",
    "strategy_monitor": "strategy",
    "factory_review": "factory",
    "rotation": "engineering_ai",
    "rotating": "engineering_ai",
    "ai_engineering": "engineering_ai",
    "weekly": "seven_agent",
    "seven": "seven_agent",
    "joint": "seven_agent",
}


def normalise_key(value: str) -> str:
    key = str(value or "").strip().lower().replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in REPORTS:
        raise ValueError("unknown report; use trade, engineering, strategy, factory, engineering_ai or seven_agent")
    return key


def _root(app) -> Path:
    path = Path(app.data_dir) / "monitor_factory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _config_path(app) -> Path:
    return _root(app) / "report_schedule.json"


def _state_path(app) -> Path:
    return _root(app) / "report_schedule_state.json"


def _atomic(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def defaults() -> dict[str, int]:
    return {key: int(row["default_hours"]) for key, row in REPORTS.items()}


def load_schedule(app) -> dict[str, int]:
    schedule = defaults()
    try:
        raw = json.loads(_config_path(app).read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    for key in REPORTS:
        try:
            hours = int((raw.get("hours") or {}).get(key, schedule[key]))
        except Exception:
            hours = schedule[key]
        schedule[key] = max(MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, hours))
    return schedule


def save_schedule(app, schedule: dict[str, int], *, changed_by: str = "system", now: int | None = None) -> dict:
    now = int(now or time.time())
    clean = defaults()
    for key in REPORTS:
        clean[key] = max(MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, int(schedule.get(key, clean[key]))))
    payload = {"schema_version": 1, "updated_epoch": now, "changed_by": str(changed_by)[:120], "hours": clean}
    _atomic(_config_path(app), payload)
    return payload


def set_interval(app, report: str, hours: int, *, changed_by: str) -> dict:
    key = normalise_key(report)
    hours = int(hours)
    if hours < MIN_INTERVAL_HOURS:
        raise ValueError(f"automatic report interval cannot be less than {MIN_INTERVAL_HOURS} hours")
    if hours > MAX_INTERVAL_HOURS:
        raise ValueError(f"automatic report interval cannot exceed {MAX_INTERVAL_HOURS} hours")
    schedule = load_schedule(app)
    schedule[key] = hours
    payload = save_schedule(app, schedule, changed_by=changed_by)
    return {"key": key, "hours": hours, "schedule": payload}


def load_state(app, *, now: int | None = None) -> dict:
    now = int(now or time.time())
    try:
        state = json.loads(_state_path(app).read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}
    runs = state.setdefault("reports", {})
    if not state.get("initialised_epoch"):
        state["schema_version"] = 1
        state["initialised_epoch"] = now
        for key in REPORTS:
            runs[key] = {"last_attempt_epoch": now, "last_success_epoch": 0, "last_status": "INITIALISED"}
        _atomic(_state_path(app), state)
    else:
        for key in REPORTS:
            runs.setdefault(key, {"last_attempt_epoch": int(state.get("initialised_epoch") or now), "last_success_epoch": 0, "last_status": "INITIALISED"})
    return state


def mark_attempt(app, report: str, *, manual: bool, now: int | None = None) -> dict:
    key = normalise_key(report)
    now = int(now or time.time())
    state = load_state(app, now=now)
    row = state["reports"].setdefault(key, {})
    row["last_attempt_epoch"] = now
    row["last_manual"] = bool(manual)
    row["last_status"] = "RUNNING"
    _atomic(_state_path(app), state)
    return row


def mark_result(app, report: str, *, success: bool, detail: str = "", now: int | None = None) -> dict:
    key = normalise_key(report)
    now = int(now or time.time())
    state = load_state(app, now=now)
    row = state["reports"].setdefault(key, {})
    row["last_status"] = "SUCCESS" if success else "FAILED"
    row["last_result_epoch"] = now
    row["detail"] = str(detail or "")[-1200:]
    if success:
        row["last_success_epoch"] = now
    _atomic(_state_path(app), state)
    return row


def due_reports(app, *, now: int | None = None) -> list[str]:
    now = int(now or time.time())
    schedule = load_schedule(app)
    state = load_state(app, now=now)
    out = []
    order = ("trade", "engineering", "strategy", "engineering_ai", "seven_agent", "factory")
    for key in order:
        row = (state.get("reports") or {}).get(key) or {}
        base = max(int(row.get("last_attempt_epoch") or 0), int(row.get("last_success_epoch") or 0))
        if now - base >= int(schedule[key]) * 3600:
            out.append(key)
    return out


def snapshot(app, *, now: int | None = None) -> dict:
    now = int(now or time.time())
    schedule = load_schedule(app)
    state = load_state(app, now=now)
    rows = []
    for key, meta in REPORTS.items():
        run = (state.get("reports") or {}).get(key) or {}
        base = max(int(run.get("last_attempt_epoch") or 0), int(run.get("last_success_epoch") or 0))
        next_due = base + int(schedule[key]) * 3600
        rows.append({"key": key, "label": meta["label"], "hours": int(schedule[key]), "mode": meta["mode"], "last_status": str(run.get("last_status") or "NEVER"), "last_attempt_epoch": int(run.get("last_attempt_epoch") or 0), "last_success_epoch": int(run.get("last_success_epoch") or 0), "next_due_epoch": next_due, "due": now >= next_due})
    return {"schema_version": 1, "generated_epoch": now, "minimum_automatic_hours": MIN_INTERVAL_HOURS, "reports": rows}
