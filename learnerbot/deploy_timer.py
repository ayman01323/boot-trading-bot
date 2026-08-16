from __future__ import annotations

import html
import os
import re
import subprocess
from pathlib import Path

TIMER_PATH = Path("/etc/systemd/system/boot-auto-deploy.timer")
TIMER_NAME = "boot-auto-deploy.timer"
ALLOWED_SECONDS = (20, 30, 60, 120, 300)


def _render_timer(seconds: int) -> str:
    return "\n".join([
        "[Unit]",
        "Description=Check BOOT GitHub challenge-auto for guarded updates",
        "",
        "[Timer]",
        "OnBootSec=20s",
        f"OnUnitActiveSec={int(seconds)}s",
        "AccuracySec=1s",
        "RandomizedDelaySec=0",
        "Unit=boot-auto-deploy.service",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def current_deploy_timer_seconds(default: int = 60) -> int:
    try:
        text = TIMER_PATH.read_text(encoding="utf-8")
    except Exception:
        return int(default)
    m = re.search(r"^OnUnitActiveSec\s*=\s*(\d+)\s*s?\s*$", text, flags=re.M | re.I)
    if not m:
        return int(default)
    return int(m.group(1))


def set_deploy_timer_seconds(seconds: int) -> dict:
    seconds = int(seconds)
    if seconds not in ALLOWED_SECONDS:
        raise ValueError("Allowed deploy timer values: " + ", ".join(str(x) for x in ALLOWED_SECONDS) + " seconds")

    TIMER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TIMER_PATH.with_suffix(".timer.tmp")
    tmp.write_text(_render_timer(seconds), encoding="utf-8")
    os.replace(tmp, TIMER_PATH)

    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=20)
    subprocess.run(["systemctl", "restart", TIMER_NAME], check=True, timeout=20)
    active = subprocess.run(
        ["systemctl", "is-active", "--quiet", TIMER_NAME],
        timeout=10,
    ).returncode == 0
    if not active:
        raise RuntimeError(f"{TIMER_NAME} did not become active")
    return {"seconds": seconds, "active": True}


def deploy_timer_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "⚡ 20 sec", "callback_data": "deploytimer:set:20"},
                {"text": "30 sec", "callback_data": "deploytimer:set:30"},
                {"text": "1 min", "callback_data": "deploytimer:set:60"},
            ],
            [
                {"text": "2 min", "callback_data": "deploytimer:set:120"},
                {"text": "5 min", "callback_data": "deploytimer:set:300"},
            ],
            [{"text": "⬅️ Menu", "callback_data": "menu:home"}],
        ]
    }


def deploy_timer_page() -> str:
    sec = current_deploy_timer_seconds()
    return (
        "<b>⏱ BOOT AUTO-DEPLOY TIMER</b>\n\n"
        f"Current GitHub check interval: <b>{sec} seconds</b>\n"
        "Branch: <code>challenge-auto</code>\n\n"
        "This timer only checks for approved GitHub code changes. Every detected update still goes through compile/tests, learnerbot restart verification and rollback protection.\n\n"
        "<i>MASTER only. Minimum interval is 20 seconds.</i>"
    )


def install_telegram_patch() -> None:
    """Add a MASTER-only deploy-timer page/command without invasive telegram_ui edits."""
    from . import telegram_ui as ui

    if getattr(ui, "_deploy_timer_patch_installed", False):
        return

    original_menu_keyboard = ui.menu_keyboard
    original_handle_update = ui.handle_update

    def menu_keyboard(app=None, chat_id=None):
        kb = original_menu_keyboard(app, chat_id)
        try:
            if app is not None and chat_id is not None and ui._master(app, chat_id):
                rows = list((kb or {}).get("inline_keyboard") or [])
                pos = 1 if rows else 0
                rows.insert(pos, [{"text": "⏱ Auto-Deploy Timer", "callback_data": "deploytimer:show"}])
                return {"inline_keyboard": rows}
        except Exception:
            pass
        return kb

    def handle_update(app, update):
        cb = update.get("callback_query") or {}
        data = str(cb.get("data") or "")
        if data.startswith("deploytimer:"):
            chat_id = (((cb.get("message") or {}).get("chat") or {}).get("id"))
            cqid = cb.get("id")
            if not ui._auth(app, chat_id):
                if cqid:
                    ui.answer_callback_query(app.telegram_bot_token, cqid, "Not authorised.")
                return
            if not ui._master(app, chat_id):
                if cqid:
                    ui.answer_callback_query(app.telegram_bot_token, cqid, "MASTER only")
                return
            if cqid:
                ui.answer_callback_query(app.telegram_bot_token, cqid)
            try:
                if data == "deploytimer:show":
                    ui._send(app, chat_id, deploy_timer_page(), deploy_timer_keyboard())
                    return
                if data.startswith("deploytimer:set:"):
                    seconds = int(data.rsplit(":", 1)[1])
                    result = set_deploy_timer_seconds(seconds)
                    ui.audit(app.csv_dir, chat_id, "DEPLOY_TIMER", "boot-auto-deploy.timer", "", str(seconds), "MASTER Telegram timer change")
                    ui._send(
                        app,
                        chat_id,
                        f"✅ GitHub auto-deploy timer changed to <b>{result['seconds']} seconds</b>.",
                        deploy_timer_keyboard(),
                    )
                    return
            except Exception as exc:
                ui._send(app, chat_id, f"❌ Deploy timer change failed: {html.escape(str(exc))}", deploy_timer_keyboard())
                return

        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat_id = (message.get("chat") or {}).get("id")
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if command == "/deploytimer":
            if not ui._auth(app, chat_id):
                return
            try:
                ui._require_master(app, chat_id)
                parts = text.split()
                if len(parts) == 1:
                    ui._send(app, chat_id, deploy_timer_page(), deploy_timer_keyboard())
                    return
                if len(parts) != 2:
                    raise ValueError("Use /deploytimer or /deploytimer 20|30|60|120|300")
                result = set_deploy_timer_seconds(int(parts[1]))
                ui.audit(app.csv_dir, chat_id, "DEPLOY_TIMER", "boot-auto-deploy.timer", "", str(result["seconds"]), "MASTER Telegram timer change")
                ui._send(app, chat_id, f"✅ GitHub auto-deploy timer changed to <b>{result['seconds']} seconds</b>.", deploy_timer_keyboard())
            except Exception as exc:
                ui._send(app, chat_id, f"❌ Deploy timer change failed: {html.escape(str(exc))}", deploy_timer_keyboard())
            return

        return original_handle_update(app, update)

    ui.menu_keyboard = menu_keyboard
    ui.handle_update = handle_update
    ui._deploy_timer_patch_installed = True


def ensure_default_20_seconds() -> dict:
    if current_deploy_timer_seconds() == 20:
        return {"seconds": 20, "active": True, "changed": False}
    result = set_deploy_timer_seconds(20)
    result["changed"] = True
    return result
