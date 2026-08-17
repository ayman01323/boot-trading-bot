from __future__ import annotations

import html
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import telegram_ui as _ui

_original_menu_keyboard = _ui.menu_keyboard
_original_handle_update = _ui.handle_update


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(_repo_root()), *args],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        ).strip()
    except Exception:
        return "unknown"


def _deploy_log_status() -> dict:
    path = Path("/var/log/boot-github-deploy.log")
    out = {"last_success": "unavailable", "seconds_ago": None, "last_event": "unavailable"}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-600:]
    except Exception:
        return out

    for line in reversed(lines):
        if out["last_event"] == "unavailable" and any(x in line for x in ("SUCCESS ", "ROLLBACK ", "REFUSED ", "ALREADY DEPLOYED ", "BEGIN ")):
            out["last_event"] = line
        if " SUCCESS " in f" {line} ":
            out["last_success"] = line
            try:
                stamp = line.split(" ", 1)[0]
                dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                out["seconds_ago"] = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
            except Exception:
                pass
            break
    return out


def menu_keyboard(app=None, chat_id=None):
    kb = _original_menu_keyboard(app, chat_id)
    rows = list(kb.get("inline_keyboard") or [])
    if app is not None and chat_id is not None and _ui._master(app, chat_id):
        if not any(any(b.get("callback_data") == "menu:autodeploy" for b in row) for row in rows):
            rows.insert(1, [{"text": "🚀 Auto Deploy", "callback_data": "menu:autodeploy"}])
    return {"inline_keyboard": rows}


def deploy_page() -> str:
    branch = _git("branch", "--show-current")
    sha = _git("rev-parse", "--short=10", "HEAD")
    origin_main = _git("rev-parse", "--short=10", "origin/main")
    status = _deploy_log_status()
    seconds = status.get("seconds_ago")
    if seconds is None:
        age = "unavailable"
    elif seconds < 60:
        age = f"{seconds}s ago"
    elif seconds < 3600:
        age = f"{seconds // 60}m {seconds % 60}s ago"
    else:
        age = f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"

    synced = sha != "unknown" and origin_main != "unknown" and sha == origin_main
    state = "🟢 DEPLOYED" if synced else "🟠 CHECKING / DIFFERENT"
    return "\n".join([
        "<b>🚀 AUTO DEPLOY</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Status      <b>{state}</b>",
        "Mode        <b>GitHub → VPS automatic</b>",
        "Trigger     <b>immediately when main changes</b>",
        "",
        f"🌿 Branch    <code>{html.escape(branch)}</code>",
        f"🧩 Server    <code>{html.escape(sha)}</code>",
        f"☁️ main      <code>{html.escape(origin_main)}</code>",
        "",
        f"⏱ Last successful deploy  <b>{html.escape(age)}</b>",
        "",
        "<i>This deploy system is push-triggered, not a periodic Git pull. The seconds value shows time since the last successful server deployment.</i>",
    ])


def deploy_keyboard():
    return {"inline_keyboard": [
        [{"text": "🔄 Refresh Auto Deploy", "callback_data": "menu:autodeploy"}],
        [{"text": "⬅️ Menu", "callback_data": "menu:home"}],
    ]}


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        cqid = cb.get("id")
        if data == "menu:autodeploy":
            if not _ui._auth(app, chat_id):
                if cqid:
                    _ui.answer_callback_query(app.telegram_bot_token, cqid, "Not authorised")
                return
            if not _ui._master(app, chat_id):
                if cqid:
                    _ui.answer_callback_query(app.telegram_bot_token, cqid, "MASTER only")
                return
            if cqid:
                _ui.answer_callback_query(app.telegram_bot_token, cqid)
            _ui._send(app, chat_id, deploy_page(), deploy_keyboard())
            return

    m = update.get("message") or {}
    chat_id = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
    if cmd == "/autodeploy":
        if not _ui._auth(app, chat_id) or not _ui._master(app, chat_id):
            return
        _ui._send(app, chat_id, deploy_page(), deploy_keyboard())
        return
    return _original_handle_update(app, update)


def install():
    if getattr(_ui, "_auto_deploy_status_patch_installed", False):
        return
    _ui.menu_keyboard = menu_keyboard
    _ui.handle_update = handle_update
    _ui._auto_deploy_status_patch_installed = True


install()
