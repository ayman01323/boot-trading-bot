from __future__ import annotations

import html
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import cli as _cli
from . import report_schedule_control as _sched
from . import telegram_ui as _ui

_PREV_APP = _cli._app
_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_SET_COMMANDS = _ui.set_commands
_STARTED = False
_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()

COMMANDS = (
    ("aireports", "View MASTER AI/report frequencies and next due times"),
    ("aifrequency", "Change a report frequency; minimum 4 hours"),
    ("airun", "Run a report/review now without changing its frequency"),
)


def _safe(value, limit=800):
    return html.escape(str(value or "")[:limit])


def _fmt_epoch(value: int) -> str:
    if not int(value or 0):
        return "never"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(value)))


def _schedule_text(app) -> str:
    snap = _sched.snapshot(app)
    lines = [
        "<b>🕒 AI / REPORT SCHEDULE</b>",
        "",
        f"Minimum automatic interval: <b>{snap['minimum_automatic_hours']}h</b>",
    ]
    for row in snap["reports"]:
        marker = "🔴" if row["due"] else "🟢"
        lines += [
            "",
            f"{marker} <b>{_safe(row['label'])}</b> — every <b>{row['hours']}h</b>",
            f"Last: {_safe(row['last_status'])} | next: <code>{_fmt_epoch(row['next_due_epoch'])}</code>",
        ]
    lines += [
        "",
        "Change: <code>/aifrequency trade 4</code>",
        "Run now: <code>/airun trade</code>",
        "Keys: <code>trade engineering strategy factory engineering_ai seven_agent</code>",
        "<i>Manual MASTER runs are allowed at any time. Automatic intervals cannot be set below 4 hours.</i>",
    ]
    return "\n".join(lines)


def _run_report(app, key: str, *, manual: bool) -> tuple[bool, str]:
    key = _sched.normalise_key(key)
    meta = _sched.REPORTS[key]
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "central_report_scheduler.py"
    _sched.mark_attempt(app, key, manual=manual)
    cmd = [sys.executable, str(script), str(meta["mode"])]
    if key == "factory":
        cmd += ["--limit", "5"]
    try:
        proc = subprocess.run(cmd, cwd=root, text=True, capture_output=True, timeout=1800, check=False)
        ok = proc.returncode == 0
        detail = (proc.stdout if ok else proc.stderr or proc.stdout)[-1200:]
    except Exception as exc:
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    _sched.mark_result(app, key, success=ok, detail=detail)
    return ok, detail


def _scheduler_loop(app):
    time.sleep(30)
    while True:
        try:
            for key in _sched.due_reports(app):
                with _RUN_LOCK:
                    ok, detail = _run_report(app, key, manual=False)
                print(f"[report-scheduler] report={key} success={str(ok).lower()} detail={detail[-300:]!r}")
        except Exception as exc:
            print(f"[report-scheduler] {type(exc).__name__}: {exc}")
        # Cheap local due-time check only. It makes no model/API call when nothing is due.
        time.sleep(300)


def _start(app):
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _sched.load_state(app)
        thread = threading.Thread(target=_scheduler_loop, args=(app,), name="report-schedule-controller", daemon=True)
        thread.start()
        _STARTED = True
        print("[report-scheduler] started check_interval=300s minimum_report_interval=4h")


def _command_set(commands):
    out = []
    seen = set()
    for row in commands or []:
        cmd = str((row or {}).get("command") or "").strip().lower()
        if not cmd or cmd in seen:
            continue
        seen.add(cmd)
        out.append({"command": cmd, "description": str((row or {}).get("description") or "")[:256]})
    for cmd, desc in COMMANDS:
        if cmd not in seen:
            out.append({"command": cmd, "description": desc})
            seen.add(cmd)
    return out[:100]


def set_commands(token: str):
    _PREV_SET_COMMANDS(token)
    try:
        from . import telegram as _tg
        from .user_registry import all_users

        csv_dir = Path(__file__).resolve().parents[1] / "CSVbot"
        for row in all_users(csv_dir):
            tid = str(row.get("telegram_id") or "").strip()
            if not tid or not tid.lstrip("-").isdigit():
                continue
            if str(row.get("role") or "").upper() != "MASTER" or str(row.get("status") or "").upper() != "ACTIVE":
                continue
            scope = {"type": "chat", "chat_id": int(tid)}
            current = _tg._json("getMyCommands", token, payload={"scope": scope}, timeout=15) or []
            _tg._json("setMyCommands", token, payload={"commands": _command_set(current), "scope": scope}, timeout=15)
    except Exception as exc:
        print(f"[telegram-report-schedule-commands] {type(exc).__name__}: {exc}")


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in {"/aireports", "/aifrequency", "/airun"}:
            try:
                _ui._require_master(app, tid)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc, 250)}")
                return
            if cmd == "/aireports":
                _ui._send(app, tid, _schedule_text(app))
                return
            if cmd == "/aifrequency":
                bits = arg.split()
                if len(bits) != 2:
                    _ui._send(app, tid, "Usage: <code>/aifrequency report hours</code>\nExample: <code>/aifrequency factory 6</code>")
                    return
                try:
                    result = _sched.set_interval(app, bits[0], int(bits[1]), changed_by=f"telegram-master:{tid}")
                    _ui._send(app, tid, f"✅ <b>{_safe(_sched.REPORTS[result['key']]['label'])}</b> set to every <b>{result['hours']}h</b>.\n\n" + _schedule_text(app))
                except Exception as exc:
                    _ui._send(app, tid, f"⚠️ {_safe(exc, 500)}")
                return
            try:
                key = _sched.normalise_key(arg)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc, 500)}")
                return
            _ui._send(app, tid, f"▶️ Running <b>{_safe(_sched.REPORTS[key]['label'])}</b> now. Automatic frequency is unchanged.")

            def worker():
                with _RUN_LOCK:
                    ok, detail = _run_report(app, key, manual=True)
                _ui._send(app, tid, ("✅" if ok else "⚠️") + f" <b>{_safe(_sched.REPORTS[key]['label'])}</b> " + ("completed" if ok else "failed") + f".\n<code>{_safe(detail, 700)}</code>")

            threading.Thread(target=worker, name=f"manual-report-{key}", daemon=True).start()
            return
    return _PREV_HANDLE_UPDATE(app, update)


def _app_with_schedule():
    app = _PREV_APP()
    try:
        _start(app)
    except Exception as exc:
        print(f"[report-scheduler-start] {type(exc).__name__}: {exc}")
    return app


def install():
    if getattr(_ui, "_telegram_report_schedule_patch_installed", False):
        return
    _ui.handle_update = handle_update
    _ui.set_commands = set_commands
    _cli._app = _app_with_schedule
    _ui._telegram_report_schedule_patch_installed = True


install()
