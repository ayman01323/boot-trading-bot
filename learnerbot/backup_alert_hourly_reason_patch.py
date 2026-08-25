from __future__ import annotations

import json
import os
import threading
import time
from datetime import date
from pathlib import Path

from . import backup_telegram_alert_patch as _alerts
from . import daily_botbuc_backup_patch as _backup

# Production hardening for BotBuc failure notifications:
# 1) reserve the hourly throttle in memory before Telegram delivery so a marker
#    write failure cannot cause one alert per 60-second monitor poll;
# 2) persist the latest backup failure reason and include it in the warning.
_INTERVAL_SECONDS = 60 * 60
_STATUS_NAME = ".backup-last-status.json"
_LOCK = threading.Lock()
_LAST_SENT_BY_DAY: dict[str, float] = {}
_LAST_REASON = ""
_INSTALLED = False

_ORIG_BACKUP_LOG = _backup._log
_ORIG_RUN_DAILY_BACKUP = _backup.run_daily_backup
_ORIG_SEND_FAILURE_ALERT = _alerts._send_failure_alert


def _status_path() -> Path:
    return _backup.BACKUP_DIR / _STATUS_NAME


def _safe_reason(value: object) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    if not text:
        return "Backup has not been verified on Google Drive; no more specific failure detail is available yet."
    lowered = text.lower()
    for marker in ("access_token=", "refresh_token=", "client_secret=", "password=", "authorization:"):
        pos = lowered.find(marker)
        if pos >= 0:
            label = marker.split("=")[0].split(":")[0]
            text = text[:pos] + label + "=[REDACTED]"
            break
    return text[:600]


def _read_status() -> dict:
    try:
        data = json.loads(_status_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_status(day: date, status: str, reason: str, target: Path | None = None) -> None:
    global _LAST_REASON
    reason = _safe_reason(reason)
    _LAST_REASON = reason if status == "failure" else ""
    path = _status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "date": day.isoformat(),
            "status": str(status),
            "reason": reason,
            "target": str(target or ""),
            "updated_epoch": int(time.time()),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception as exc:
        _ORIG_BACKUP_LOG(f"backup status persistence failed: {type(exc).__name__}: {exc}")


def _backup_log_with_reason(message: str) -> None:
    text = str(message or "")
    if (
        text.startswith("Drive upload failed")
        or text.startswith("Drive upload skipped")
        or text.startswith("backup failed:")
    ):
        _write_status(date.today(), "failure", text)
    _ORIG_BACKUP_LOG(text)


def _run_daily_backup_with_reason(today: date | None = None) -> Path:
    run_day = today or date.today()
    target = _backup.BACKUP_DIR / f"{run_day.isoformat()}.zip"
    try:
        result = _ORIG_RUN_DAILY_BACKUP(today)
    except Exception as exc:
        _write_status(run_day, "failure", f"{type(exc).__name__}: {exc}", target)
        raise

    verification = _backup._load_verification(result)
    if verification:
        _write_status(run_day, "success", "Google Drive upload verified", result)
    else:
        _write_status(
            run_day,
            "failure",
            "Backup returned without a valid Google Drive verification marker; local archive is retained for retry.",
            result,
        )
    return result


def _diagnose_failure(day: date, target: Path) -> str:
    state = _read_status()
    if str(state.get("date") or "") == day.isoformat() and str(state.get("status") or "") == "failure":
        reason = _safe_reason(state.get("reason"))
        if reason:
            return reason
    if _LAST_REASON:
        return _safe_reason(_LAST_REASON)

    try:
        ok, detail = _backup._rclone_available()
    except Exception as exc:
        return _safe_reason(f"rclone availability check failed: {type(exc).__name__}: {exc}")
    if not ok:
        return _safe_reason(detail)
    if target.exists():
        return (
            "Local ZIP exists, but the Google Drive copy has not passed remote verification "
            "(upload incomplete, remote lookup failed, or remote/local size did not match)."
        )
    return (
        "No verified Google Drive backup exists for today and the local daily ZIP is not present; "
        "the backup worker will retry automatically."
    )


def _failure_text_with_reason(day: date, target: Path) -> str:
    local_state = "present and retained" if target.exists() else "not present"
    reason = _diagnose_failure(day, target)
    return (
        "🚨 BotBuc BACKUP FAILURE / NOT VERIFIED\n"
        f"Date: {day.isoformat()}\n"
        f"Google Drive target: {_backup.DRIVE_DEST}/{day.isoformat()}.zip\n"
        f"Failure reason: {reason}\n"
        f"Local ZIP: {local_state}.\n"
        "Automatic backup/upload retry continues every 1 hour.\n"
        "This Telegram alert will repeat no more than once every 1 hour until Drive verification succeeds."
    )


def _failure_alert_due_hourly(day: date, now_epoch: float) -> bool:
    key = day.isoformat()
    with _LOCK:
        memory_last = float(_LAST_SENT_BY_DAY.get(key, 0.0) or 0.0)
    state = _alerts._read_json(_alerts._failure_delivery_marker(day))
    try:
        disk_last = float(state.get("last_sent_epoch", 0) or 0)
    except (TypeError, ValueError):
        disk_last = 0.0
    last = max(memory_last, disk_last)
    return last <= 0 or float(now_epoch) - last >= _INTERVAL_SECONDS


def _send_failure_alert_hourly(app, day: date, target: Path, now_epoch: float) -> bool:
    key = day.isoformat()
    # Reserve the interval BEFORE any Telegram or filesystem operation. This is
    # the crucial guard against one-message-per-minute storms if marker writes fail.
    with _LOCK:
        _LAST_SENT_BY_DAY[key] = float(now_epoch)
    try:
        return bool(_ORIG_SEND_FAILURE_ALERT(app, day, target, now_epoch))
    except Exception as exc:
        _alerts._log(
            "failure alert delivery/state error suppressed until next hourly window: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _backup._log = _backup_log_with_reason
    _backup.run_daily_backup = _run_daily_backup_with_reason
    _alerts.FAILURE_ALERT_INTERVAL_SECONDS = _INTERVAL_SECONDS
    _alerts._failure_text = _failure_text_with_reason
    _alerts._failure_alert_due = _failure_alert_due_hourly
    _alerts._send_failure_alert = _send_failure_alert_hourly
    _INSTALLED = True
    _alerts._log("backup alert hardening installed: failure alerts hourly + failure reason included")


install()
