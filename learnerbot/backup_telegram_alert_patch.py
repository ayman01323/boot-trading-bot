from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from . import cli as _cli
from . import daily_botbuc_backup_patch as _backup
from .telegram import send_to_chats
from .user_registry import all_users

_STARTED = False
_LOCK = threading.Lock()
_PREV_APP = _cli._app

POLL_SECONDS = 60
FAILURE_ALERT_INTERVAL_SECONDS = 60 * 60
FIRST_FAILURE_HOUR_LOCAL = 4
# Temporarily silence only BotBuc backup FAILURE Telegram alerts. The backup
# worker/retries and the one-off SUCCESS verification notification remain active.
FAILURE_ALERTS_ENABLED = False


def _log(message: str) -> None:
    print(f"[botbuc-telegram-alert] {message}", flush=True)


def _master_chat_ids(app) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for row in all_users(app.csv_dir, enabled_only=True):
        if str(row.get("status") or "").upper() != "ACTIVE":
            continue
        if str(row.get("role") or "").upper() != "MASTER":
            continue
        tid = str(row.get("telegram_id") or "").strip()
        if tid and tid not in seen:
            seen.add(tid)
            ids.append(tid)
    return ids


def _success_delivery_marker(day) -> Path:
    return _backup.BACKUP_DIR / f".{day.isoformat()}.telegram-backup-success.json"


def _failure_delivery_marker(day) -> Path:
    return _backup.BACKUP_DIR / f".{day.isoformat()}.telegram-backup-failure.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _send_to_master_ids(app, chat_ids: list[str], text: str) -> dict:
    token = str(getattr(app, "telegram_bot_token", "") or "").strip()
    if not token:
        _log("Telegram notification skipped: TELEGRAM_BOT_TOKEN is not configured")
        return {"sent_chats": 0, "failed_chats": len(chat_ids), "details": []}
    if not chat_ids:
        _log("Telegram notification skipped: no active MASTER Telegram IDs")
        return {"sent_chats": 0, "failed_chats": 0, "details": []}
    try:
        return send_to_chats(token, chat_ids, text, protect_content=True)
    except Exception as exc:
        _log(f"Telegram notification failed: {type(exc).__name__}: {exc}")
        return {"sent_chats": 0, "failed_chats": len(chat_ids), "details": []}


def _success_text(day, verification: dict) -> str:
    size = int(verification.get("size") or 0)
    return (
        "✅ BotBuc BACKUP SUCCESS\n"
        f"Date: {day.isoformat()}\n"
        f"Google Drive: {_backup.DRIVE_DEST}/{day.isoformat()}.zip\n"
        f"Verified size: {size:,} bytes\n"
        f"Local server ZIP retention: {_backup.LOCAL_RETENTION_HOURS} hours after verification."
    )


def _failure_text(day, target: Path) -> str:
    local_state = "present and retained" if target.exists() else "not present"
    return (
        "🚨 BotBuc BACKUP FAILURE / NOT VERIFIED\n"
        f"Date: {day.isoformat()}\n"
        f"Google Drive target: {_backup.DRIVE_DEST}/{day.isoformat()}.zip\n"
        "The daily backup has not been verified on Google Drive.\n"
        f"Local ZIP: {local_state}.\n"
        "Automatic backup/upload retry continues every 1 hour.\n"
        "This Telegram alert will repeat every 1 hour until Drive verification succeeds."
    )


def _send_success_once(app, day, verification: dict) -> bool:
    masters = _master_chat_ids(app)
    marker = _success_delivery_marker(day)
    state = _read_json(marker)
    delivered = {str(x) for x in state.get("delivered_master_ids", []) if str(x).strip()}
    missing = [tid for tid in masters if tid not in delivered]
    if not missing:
        return bool(masters)

    result = _send_to_master_ids(app, missing, _success_text(day, verification))
    for detail in result.get("details", []):
        if detail.get("ok"):
            delivered.add(str(detail.get("chat_id")))
    if delivered:
        _write_json(
            marker,
            {
                "date": day.isoformat(),
                "remote": f"{_backup.DRIVE_DEST}/{day.isoformat()}.zip",
                "delivered_master_ids": sorted(delivered),
                "updated_epoch": int(time.time()),
            },
        )
    all_delivered = bool(masters) and all(tid in delivered for tid in masters)
    _log(
        f"success notification masters={len(masters)} delivered={len(delivered)} "
        f"complete={str(all_delivered).lower()}"
    )
    return all_delivered


def _failure_alert_due(day, now_epoch: float) -> bool:
    state = _read_json(_failure_delivery_marker(day))
    try:
        last = float(state.get("last_sent_epoch", 0))
    except (TypeError, ValueError):
        last = 0.0
    return last <= 0 or now_epoch - last >= FAILURE_ALERT_INTERVAL_SECONDS


def _send_failure_alert(app, day, target: Path, now_epoch: float) -> bool:
    masters = _master_chat_ids(app)
    result = _send_to_master_ids(app, masters, _failure_text(day, target))
    attempted = bool(masters) and bool(str(getattr(app, "telegram_bot_token", "") or "").strip())
    if attempted:
        _write_json(
            _failure_delivery_marker(day),
            {
                "date": day.isoformat(),
                "last_sent_epoch": int(now_epoch),
                "sent_chats": int(result.get("sent_chats", 0)),
                "failed_chats": int(result.get("failed_chats", 0)),
            },
        )
    _log(
        f"failure notification masters={len(masters)} sent={int(result.get('sent_chats', 0))} "
        f"failed={int(result.get('failed_chats', 0))}"
    )
    return int(result.get("sent_chats", 0)) > 0


def check_backup_alerts(app, now: datetime | None = None) -> str:
    now = now or datetime.now()
    day = now.date()
    target = _backup.BACKUP_DIR / f"{day.isoformat()}.zip"
    verification = _backup._load_verification(target)

    if verification:
        _send_success_once(app, day, verification)
        return "success"

    if not FAILURE_ALERTS_ENABLED:
        return "failure-alerts-disabled"

    first_failure = now.replace(
        hour=FIRST_FAILURE_HOUR_LOCAL,
        minute=0,
        second=0,
        microsecond=0,
    )
    if now < first_failure:
        return "waiting"

    now_epoch = now.timestamp()
    if _failure_alert_due(day, now_epoch):
        _send_failure_alert(app, day, target, now_epoch)
        return "failure-alert"
    return "failure-wait"


def _worker(app) -> None:
    # The backup worker starts with a 20-second delay. Give it a little room on
    # service restart before checking state, then monitor once per minute.
    time.sleep(90)
    while True:
        try:
            check_backup_alerts(app)
        except Exception as exc:
            _log(f"monitor error: {type(exc).__name__}: {exc}")
        time.sleep(POLL_SECONDS)


def start_backup_alert_thread(app) -> threading.Thread | None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return None
        thread = threading.Thread(
            target=_worker,
            args=(app,),
            name="botbuc-telegram-alerts",
            daemon=True,
        )
        thread.start()
        _STARTED = True
        _log("MASTER success alerts enabled; backup failure alerts temporarily disabled")
        return thread


def _app_with_backup_alerts():
    app = _PREV_APP()
    start_backup_alert_thread(app)
    return app


_cli._app = _app_with_backup_alerts
