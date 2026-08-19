from __future__ import annotations

import html
import json
import threading
import time
from pathlib import Path

from . import cli as _cli
from . import telegram as _tg
from . import telegram_ui as _ui
from .ai_ops_status import (
    decision_rows,
    latest_master_decision,
    load_sent_state,
    master_chat_ids,
    notification_state,
    save_sent_state,
    snapshot_for_display,
    transition_messages,
)
from .user_registry import all_users

_PREV_APP = _cli._app
_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_SET_COMMANDS = _ui.set_commands
_THREAD_LOCK = threading.Lock()
_THREAD_STARTED = False

AI_MASTER_COMMANDS = (
    ("aiaudit", "MASTER three-agent engineering audit status"),
    ("aidecision", "MASTER GPT accept/reject/defer ledger"),
    ("aistrategy", "MASTER three-agent strategy review status"),
    ("aiupdates", "MASTER combined AI operations status"),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _state_path(app) -> Path:
    return Path(app.data_dir) / ".ai_ops_telegram_state.json"


def _safe(value, limit=900):
    return html.escape(str(value or "")[:limit])


def _agent_icon(value: str) -> str:
    value = str(value or "WAITING").upper()
    if value == "DONE":
        return "✅"
    if value == "INCOMPLETE":
        return "⚠️"
    return "⏳"


def _engineering_text(state: dict) -> str:
    e = (state or {}).get("engineering") or {}
    if not e.get("available"):
        return "<b>🧪 AI ENGINEERING AUDIT</b>\n\nNo engineering audit has been published yet."
    counts = e.get("decision_counts") or {}
    lines = [
        "<b>🧪 AI ENGINEERING AUDIT</b>",
        "",
        f"Source: <code>{_safe(str(e.get('source_commit') or '')[:12])}</code>",
        f"GPT: {_agent_icon(e.get('gpt'))} <b>{_safe(e.get('gpt'))}</b>",
        f"Gemini: {_agent_icon(e.get('gemini'))} <b>{_safe(e.get('gemini'))}</b>",
        f"Copilot: {_agent_icon(e.get('copilot'))} <b>{_safe(e.get('copilot'))}</b>",
        f"All three complete: <b>{'YES' if e.get('three_agent_reports_complete') else 'NO'}</b>",
    ]
    if e.get("master_decision_available"):
        lines += [
            "",
            f"GPT master: <b>{_safe(e.get('master_status'))}</b>",
            f"ACCEPT <b>{counts.get('ACCEPT',0)}</b> | REJECT <b>{counts.get('REJECT',0)}</b> | DEFER <b>{counts.get('DEFER',0)}</b>",
            f"Policy-approved fixes: <b>{int(e.get('policy_accepted_count') or 0)}</b>",
        ]
    if e.get("corrective_pr_url"):
        lines += ["", f"Corrective draft PR: {_safe(e.get('corrective_pr_url'), 300)}"]
    lines += ["", "Use <code>/aidecision</code> to see why GPT accepted, rejected or deferred findings."]
    return "\n".join(lines)


def _decision_text(state: dict, filter_name: str = "") -> str:
    e = (state or {}).get("engineering") or {}
    if not e.get("master_decision_available"):
        return "<b>🧠 GPT MASTER DECISION</b>\n\nNot available yet. All three engineering reports must complete first."
    master = latest_master_decision(_repo_root()) or {}
    counts = e.get("decision_counts") or {}
    wanted = str(filter_name or "").upper().strip()
    if wanted not in {"ACCEPT", "REJECT", "DEFER"}:
        wanted = ""
    lines = [
        "<b>🧠 GPT MASTER ENGINEERING DECISION</b>",
        f"Status: <b>{_safe(e.get('master_status'))}</b>",
        f"ACCEPT <b>{counts.get('ACCEPT',0)}</b> | REJECT <b>{counts.get('REJECT',0)}</b> | DEFER <b>{counts.get('DEFER',0)}</b>",
        "",
        _safe(e.get("master_summary") or "No summary supplied.", 800),
    ]
    rows = decision_rows(master, wanted or None, limit=8)
    if rows:
        lines.append("")
        lines.append(f"<b>{_safe(wanted or 'Latest decisions')}:</b>")
        for row in rows:
            marker = {"ACCEPT": "✅", "REJECT": "❌", "DEFER": "⏸"}.get(row.get("disposition"), "•")
            lines.append(
                f"{marker} <b>{_safe(row.get('severity'))} {_safe(row.get('title'),220)}</b>\n"
                f"Reason: {_safe(row.get('reason'),550)}"
            )
            if row.get("disposition") == "ACCEPT" and not row.get("policy_eligible"):
                reasons = "; ".join(row.get("policy_reasons") or [])
                lines.append(f"Policy override: {_safe(reasons,450)}")
    lines += [
        "",
        "<i>GPT is not the final safety authority. The deterministic policy gate can block GPT, and accepted fixes still require tests and a draft PR.</i>",
        "Filter with <code>/aidecision accept</code>, <code>/aidecision reject</code> or <code>/aidecision defer</code>.",
    ]
    return "\n".join(lines)


def _strategy_text(state: dict) -> str:
    s = (state or {}).get("strategy") or {}
    if not s.get("available"):
        return (
            "<b>🔬 THREE-AGENT STRATEGY REVIEW</b>\n\n"
            "Waiting for the first sequenced GPT + Gemini + Copilot strategy cycle. "
            "The hourly Gemini-only research review is not counted as a three-agent completion."
        )
    counts = s.get("decision_counts") or {}
    lines = [
        "<b>🔬 THREE-AGENT STRATEGY REVIEW</b>",
        "",
        f"Cycle: <code>{_safe(s.get('cycle_id'),120)}</code>",
        f"GPT: {_agent_icon(s.get('gpt'))} <b>{_safe(s.get('gpt'))}</b>",
        f"Gemini: {_agent_icon(s.get('gemini'))} <b>{_safe(s.get('gemini'))}</b>",
        f"Copilot: {_agent_icon(s.get('copilot'))} <b>{_safe(s.get('copilot'))}</b>",
        f"All three complete: <b>{'YES' if s.get('three_agent_reports_complete') else 'NO'}</b>",
    ]
    if s.get("master_decision_available"):
        lines += ["", f"ACCEPT {counts.get('ACCEPT',0)} | REJECT {counts.get('REJECT',0)} | DEFER {counts.get('DEFER',0)}"]
    if s.get("change_pr_url"):
        lines += ["", f"Strategy change draft PR: {_safe(s.get('change_pr_url'),300)}"]
    lines += ["", "<i>New/changed strategies remain shadow-first and are never auto-deployed live by this review lane.</i>"]
    return "\n".join(lines)


def _combined_text(state: dict) -> str:
    return _engineering_text(state) + "\n\n────────────\n\n" + _strategy_text(state)


def _command_set(commands):
    out = []
    seen = set()
    for row in commands or []:
        cmd = str((row or {}).get("command") or "").strip().lower()
        if not cmd or cmd in seen:
            continue
        seen.add(cmd)
        out.append({"command": cmd, "description": str((row or {}).get("description") or "")[:256]})
    for cmd, desc in AI_MASTER_COMMANDS:
        if cmd not in seen:
            out.append({"command": cmd, "description": desc})
            seen.add(cmd)
    return out[:100]


def set_commands(token: str):
    _PREV_SET_COMMANDS(token)
    try:
        csv_dir = _repo_root() / "CSVbot"
        for row in all_users(csv_dir):
            tid = str(row.get("telegram_id") or "").strip()
            if not tid or not tid.lstrip("-").isdigit():
                continue
            if str(row.get("role") or "USER").upper() != "MASTER" or str(row.get("status") or "").upper() != "ACTIVE":
                continue
            scope = {"type": "chat", "chat_id": int(tid)}
            current = _tg._json("getMyCommands", token, payload={"scope": scope}, timeout=15) or []
            _tg._json("setMyCommands", token, payload={"commands": _command_set(current), "scope": scope}, timeout=15)
    except Exception as exc:
        print(f"[telegram-ai-ops-commands] {type(exc).__name__}: {exc}")


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in {"/aiaudit", "/aiagents", "/aidecision", "/aistrategy", "/aiupdates"}:
            try:
                _ui._require_master(app, tid)
            except Exception as exc:
                _ui._send(app, tid, f"⚠️ {_safe(exc,250)}")
                return
            state = snapshot_for_display(_repo_root())
            if cmd in {"/aiaudit", "/aiagents"}:
                body = _engineering_text(state)
            elif cmd == "/aidecision":
                body = _decision_text(state, arg)
            elif cmd == "/aistrategy":
                body = _strategy_text(state)
            else:
                body = _combined_text(state)
            if not state.get("fetch_ok"):
                body += "\n\n⚠️ Latest ai-reviews fetch failed; showing the newest locally cached remote state."
            _ui._send(app, tid, body)
            return
    return _PREV_HANDLE_UPDATE(app, update)


def _watch_loop(app):
    time.sleep(8)
    state_file = _state_path(app)
    previous = load_sent_state(state_file)
    while True:
        try:
            current = snapshot_for_display(_repo_root())
            # Do not publish git transport errors to Telegram repeatedly; keep them in logs.
            if current.get("fetch_ok"):
                for text in transition_messages(previous, current):
                    masters = master_chat_ids(app.csv_dir)
                    if masters and app.telegram_bot_token:
                        _tg.send_to_chats(app.telegram_bot_token, masters, text, disable_notification=False)
                save_sent_state(state_file, current)
                previous = current
            else:
                print(f"[ai-ops-watcher] fetch failed: {current.get('fetch_detail')}")
        except Exception as exc:
            print(f"[ai-ops-watcher] {type(exc).__name__}: {exc}")
        time.sleep(60)


def _start_watcher(app):
    global _THREAD_STARTED
    with _THREAD_LOCK:
        if _THREAD_STARTED:
            return
        if not getattr(app, "telegram_bot_token", ""):
            return
        thread = threading.Thread(target=_watch_loop, args=(app,), name="ai-ops-telegram-watcher", daemon=True)
        thread.start()
        _THREAD_STARTED = True
        print("[ai-ops-watcher] started interval=60s master-role-dynamic=true")


def _app_with_ai_ops():
    app = _PREV_APP()
    try:
        _start_watcher(app)
    except Exception as exc:
        print(f"[ai-ops-watcher-start] {type(exc).__name__}: {exc}")
    return app


def install():
    if getattr(_ui, "_telegram_ai_ops_patch_installed", False):
        return
    _ui.handle_update = handle_update
    _ui.set_commands = set_commands
    _cli._app = _app_with_ai_ops
    _ui._telegram_ai_ops_patch_installed = True


install()
