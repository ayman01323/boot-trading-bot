from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from . import daily_botbuc_backup_patch as _backup

STATUS_FILE = Path("/tmp/learnerbot-botbuc-status.json")
_PREV_UPLOAD = _backup.upload_to_drive
_PREV_RUN = _backup.run_daily_backup
_LAST_DRIVE: bool | None = None


def _production_run_command() -> bool:
    args = sys.argv[1:]
    return bool(args and args[0] == "run")


def _write(stage: str, *, archive: Path | None = None, drive_uploaded: bool | None = None, detail: str = "") -> None:
    if not _production_run_command():
        return
    payload = {
        "updated_epoch": int(time.time()),
        "stage": str(stage),
        "source": str(_backup.SOURCE),
        "backup_dir": str(_backup.BACKUP_DIR),
        "retention_server_days": int(_backup.RETENTION_DAYS),
        "drive_retention": "unlimited",
        "drive_destination": str(_backup.DRIVE_DEST),
        "archive": str(archive) if archive else "",
        "archive_exists": bool(archive and archive.is_file()),
        "archive_bytes": int(archive.stat().st_size) if archive and archive.is_file() else 0,
        "drive_uploaded": drive_uploaded,
        "detail": str(detail)[:500],
    }
    tmp = STATUS_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATUS_FILE)
    except Exception:
        pass


def upload_to_drive_with_status(archive: Path) -> bool:
    global _LAST_DRIVE
    result = _PREV_UPLOAD(archive)
    _LAST_DRIVE = bool(result)
    _write("drive_uploaded" if result else "drive_not_uploaded", archive=archive, drive_uploaded=bool(result))
    return result


def run_daily_backup_with_status(today=None):
    global _LAST_DRIVE
    _LAST_DRIVE = None
    _write("backup_starting")
    try:
        archive = _PREV_RUN(today)
    except Exception as exc:
        _write("backup_failed", detail=f"{type(exc).__name__}: {exc}")
        raise
    _write("backup_complete", archive=archive, drive_uploaded=_LAST_DRIVE)
    return archive


_backup.upload_to_drive = upload_to_drive_with_status
_backup.run_daily_backup = run_daily_backup_with_status

if _production_run_command():
    try:
        _write(
            "worker_started",
            detail=("backup_dir_ready" if _backup.BACKUP_DIR.is_dir() else "backup_dir_not_ready"),
        )
    except OSError:
        _write("worker_started", detail="backup_dir_probe_failed")
