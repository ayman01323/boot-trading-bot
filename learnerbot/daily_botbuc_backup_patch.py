from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

# User-requested full bot backup policy.
SOURCE = Path("/root/multichain-learning-bot-v2.2-fast-direct-market")
BACKUP_DIR = Path("/root/BotBuc")
RCLONE_CONFIG = Path("/root/.config/rclone/rclone.conf")
DRIVE_DEST = "ndrive:BotBuc"
LOCAL_RETENTION_HOURS = 2
MAX_LOCAL_ZIPS = 1
DAILY_HOUR_LOCAL = 3
RETRY_SECONDS = 60 * 60
_INITIAL_DELAY_SECONDS = 20
_STARTED = False
_LOCK = threading.Lock()


def _log(message: str) -> None:
    print(f"[daily-botbuc-backup] {message}", flush=True)


def _ensure_backup_dir() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        BACKUP_DIR.chmod(0o700)
    except OSError:
        pass


def _zip_symlink(zf: zipfile.ZipFile, path: Path, arcname: str) -> None:
    info = zipfile.ZipInfo(arcname)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    info.compress_type = zipfile.ZIP_STORED
    zf.writestr(info, os.readlink(path))


def _build_zip(target: Path) -> None:
    """Create one atomic ZIP containing the complete bot folder tree."""
    if not SOURCE.is_dir():
        raise FileNotFoundError(f"bot source folder not found: {SOURCE}")

    tmp = BACKUP_DIR / f".{target.name}.tmp"
    try:
        tmp.unlink(missing_ok=True)
    except TypeError:  # pragma: no cover - Python <3.8 compatibility
        if tmp.exists():
            tmp.unlink()

    skipped = 0
    with zipfile.ZipFile(
        tmp,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as zf:
        source_parent = SOURCE.parent
        for root, dirs, files in os.walk(SOURCE, topdown=True, followlinks=False):
            dirs.sort()
            files.sort()
            root_path = Path(root)

            root_arc = root_path.relative_to(source_parent).as_posix().rstrip("/") + "/"
            try:
                zf.write(root_path, root_arc)
            except (FileNotFoundError, PermissionError, OSError):
                skipped += 1

            keep_dirs: list[str] = []
            for dname in dirs:
                dpath = root_path / dname
                if dpath.is_symlink():
                    arc = dpath.relative_to(source_parent).as_posix()
                    try:
                        _zip_symlink(zf, dpath, arc)
                    except (FileNotFoundError, PermissionError, OSError):
                        skipped += 1
                else:
                    keep_dirs.append(dname)
            dirs[:] = keep_dirs

            for fname in files:
                fpath = root_path / fname
                arc = fpath.relative_to(source_parent).as_posix()
                try:
                    if fpath.is_symlink():
                        _zip_symlink(zf, fpath, arc)
                    elif fpath.is_file():
                        zf.write(fpath, arc)
                    else:
                        skipped += 1
                except (FileNotFoundError, PermissionError, OSError):
                    skipped += 1

    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    _log(f"created {target} bytes={target.stat().st_size} skipped_special_or_transient={skipped}")


def _date_from_archive(path: Path) -> date | None:
    if path.suffix.lower() != ".zip":
        return None
    try:
        return datetime.strptime(path.stem, "%Y-%m-%d").date()
    except ValueError:
        return None


def _verification_marker(archive: Path) -> Path:
    return BACKUP_DIR / f".{archive.stem}.drive-verified.json"


def _load_verification(archive: Path) -> dict | None:
    marker = _verification_marker(archive)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("remote") != f"{DRIVE_DEST}/{archive.name}":
        return None
    try:
        size = int(data.get("size", -1))
        verified_at = float(data.get("verified_at", -1))
    except (TypeError, ValueError):
        return None
    if size < 0 or verified_at <= 0:
        return None
    if archive.exists():
        try:
            if archive.stat().st_size != size:
                return None
        except OSError:
            return None
    return {"remote": data["remote"], "size": size, "verified_at": verified_at}


def _record_verification(archive: Path, verified_at: float | None = None) -> dict:
    verified_at = float(verified_at if verified_at is not None else time.time())
    size = archive.stat().st_size
    existing = _load_verification(archive)
    if existing and existing["size"] == size:
        # Preserve the earliest verified time across service restarts/re-checks.
        verified_at = min(verified_at, float(existing["verified_at"]))
    data = {
        "remote": f"{DRIVE_DEST}/{archive.name}",
        "size": size,
        "verified_at": verified_at,
    }
    marker = _verification_marker(archive)
    tmp = marker.with_suffix(marker.suffix + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, marker)
    return data


def _delete_local_archive(archive: Path, reason: str) -> bool:
    try:
        archive.unlink()
        _log(f"deleted local backup {archive.name}: {reason}")
        return True
    except FileNotFoundError:
        return False


def cleanup_local_backups(now: float | None = None) -> list[Path]:
    """Delete only locally verified ZIPs whose 2-hour retention window elapsed."""
    _ensure_backup_dir()
    now = float(now if now is not None else time.time())
    cutoff_seconds = LOCAL_RETENTION_HOURS * 60 * 60
    removed: list[Path] = []
    for path in sorted(BACKUP_DIR.glob("*.zip")):
        if _date_from_archive(path) is None:
            continue
        verification = _load_verification(path)
        if not verification:
            continue
        if now - float(verification["verified_at"]) >= cutoff_seconds:
            if _delete_local_archive(
                path,
                f"Drive-verified retention of {LOCAL_RETENTION_HOURS} hours elapsed",
            ):
                removed.append(path)
    return removed


def _remote_prefix() -> str:
    return DRIVE_DEST.split(":", 1)[0] + ":"


def _rclone_available() -> tuple[bool, str]:
    exe = shutil.which("rclone")
    if not exe:
        return False, "rclone is not installed"
    if not RCLONE_CONFIG.is_file():
        return False, f"rclone config missing: {RCLONE_CONFIG}"
    try:
        cp = subprocess.run(
            [exe, "--config", str(RCLONE_CONFIG), "listremotes"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        return False, f"rclone remote check failed: {type(exc).__name__}: {exc}"
    remotes = {line.strip() for line in cp.stdout.splitlines() if line.strip()}
    required = _remote_prefix()
    if required not in remotes:
        return False, f"rclone config does not contain {required}"
    return True, exe


def _remote_copy_matches(archive: Path) -> bool:
    ok, value = _rclone_available()
    if not ok:
        return False
    exe = value
    remote = f"{DRIVE_DEST}/{archive.name}"
    try:
        cp = subprocess.run(
            [exe, "--config", str(RCLONE_CONFIG), "lsjson", remote, "--stat"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        remote_size = int(json.loads(cp.stdout).get("Size", -1))
        local_size = archive.stat().st_size
        if remote_size == local_size:
            _log(f"Google Drive already matches: {remote} bytes={local_size}")
            return True
    except Exception:
        return False
    return False


def upload_to_drive(archive: Path) -> bool:
    """Upload/replace one ZIP in Google Drive BotBuc and verify byte size."""
    ok, value = _rclone_available()
    if not ok:
        _log(f"Drive upload skipped: {value}")
        return False
    exe = value
    remote = f"{DRIVE_DEST}/{archive.name}"
    try:
        subprocess.run(
            [exe, "--config", str(RCLONE_CONFIG), "mkdir", DRIVE_DEST],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        subprocess.run(
            [exe, "--config", str(RCLONE_CONFIG), "copyto", str(archive), remote],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60 * 60,
        )
        cp = subprocess.run(
            [exe, "--config", str(RCLONE_CONFIG), "lsjson", remote, "--stat"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        remote_size = int(json.loads(cp.stdout).get("Size", -1))
        local_size = archive.stat().st_size
        if remote_size != local_size:
            raise RuntimeError(f"remote size {remote_size} != local size {local_size}")
        _log(f"Google Drive verified: {remote} bytes={local_size}")
        return True
    except Exception as exc:
        _log(f"Drive upload failed; local backup retained: {type(exc).__name__}: {exc}")
        return False


def _schedule_local_delete(archive: Path, verified_at: float) -> threading.Thread | None:
    if not archive.exists():
        return None
    deadline = float(verified_at) + LOCAL_RETENTION_HOURS * 60 * 60
    delay = max(0.0, deadline - time.time())

    def _delete_when_due() -> None:
        if delay > 0:
            time.sleep(delay)
        verification = _load_verification(archive)
        if not verification:
            return
        if time.time() - float(verification["verified_at"]) < LOCAL_RETENTION_HOURS * 60 * 60:
            return
        _delete_local_archive(
            archive,
            f"Drive-verified retention of {LOCAL_RETENTION_HOURS} hours elapsed",
        )

    if delay <= 0:
        _delete_when_due()
        return None
    thread = threading.Thread(
        target=_delete_when_due,
        name=f"botbuc-delete-{archive.stem}",
        daemon=True,
    )
    thread.start()
    _log(f"local deletion scheduled in {delay / 3600:.2f}h for {archive.name}")
    return thread


def _resolve_stale_local_archives(today: date) -> None:
    """Keep at most one ZIP locally; recover older failed uploads before a new backup."""
    for path in sorted(BACKUP_DIR.glob("*.zip")):
        archive_day = _date_from_archive(path)
        if archive_day is None or archive_day == today:
            continue

        verification = _load_verification(path)
        if verification:
            _delete_local_archive(path, "older Drive-verified backup removed before today's backup")
            continue

        if _remote_copy_matches(path) or upload_to_drive(path):
            _record_verification(path)
            _delete_local_archive(path, "older backup safely verified on Drive before today's backup")
            continue

        raise RuntimeError(
            f"older local backup {path.name} could not be uploaded; "
            f"retaining it and refusing to create another ZIP (max local ZIPs={MAX_LOCAL_ZIPS})"
        )


def run_daily_backup(today: date | None = None) -> Path:
    """Create/verify one daily ZIP, keep it locally for 2 hours, then delete it."""
    _ensure_backup_dir()
    today = today or date.today()
    target = BACKUP_DIR / f"{today.isoformat()}.zip"

    cleanup_local_backups()
    _resolve_stale_local_archives(today)

    # A tiny verification marker survives local ZIP deletion. This prevents a
    # service restart later the same day from recreating a 1.6+ GB archive.
    verification = _load_verification(target)
    if verification:
        if target.exists():
            _schedule_local_delete(target, float(verification["verified_at"]))
        else:
            _log(f"today already completed and retained on Drive: {target.name}")
        return target

    local_zips = [
        p
        for p in BACKUP_DIR.glob("*.zip")
        if _date_from_archive(p) is not None and p != target
    ]
    if len(local_zips) >= MAX_LOCAL_ZIPS:
        raise RuntimeError(
            f"local ZIP cap reached ({MAX_LOCAL_ZIPS}); refusing to create {target.name}"
        )

    if not target.is_file() or target.stat().st_size <= 0:
        _build_zip(target)
    else:
        _log(f"today's local backup already exists: {target.name} bytes={target.stat().st_size}")

    # A manual upload may already have completed. Verify first to avoid sending
    # the same large ZIP twice; otherwise upload and verify it now.
    if not (_remote_copy_matches(target) or upload_to_drive(target)):
        raise RuntimeError("Drive upload not verified; local ZIP retained for hourly retry")

    verification = _record_verification(target)
    _schedule_local_delete(target, float(verification["verified_at"]))
    return target


def _seconds_until_next_daily_run(now: datetime | None = None) -> float:
    now = now or datetime.now()
    target = now.replace(hour=DAILY_HOUR_LOCAL, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


def _worker() -> None:
    time.sleep(_INITIAL_DELAY_SECONDS)
    while True:
        failed = False
        try:
            run_daily_backup()
        except Exception as exc:
            failed = True
            _log(f"backup failed: {type(exc).__name__}: {exc}")
        if failed:
            _log(f"retrying backup/upload in {RETRY_SECONDS // 3600} hour")
            time.sleep(RETRY_SECONDS)
        else:
            time.sleep(_seconds_until_next_daily_run())


def start_daily_backup_thread() -> threading.Thread | None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return None
        _ensure_backup_dir()
        thread = threading.Thread(target=_worker, name="daily-botbuc-backup", daemon=True)
        thread.start()
        _STARTED = True
        _log(
            f"enabled source={SOURCE} local={BACKUP_DIR} drive={DRIVE_DEST} "
            f"daily={DAILY_HOUR_LOCAL:02d}:00 local_retention_hours={LOCAL_RETENTION_HOURS} "
            f"max_local_zips={MAX_LOCAL_ZIPS} drive_retention=unlimited"
        )
        return thread


def _production_run_command() -> bool:
    args = sys.argv[1:]
    if not (args and args[0] == "run"):
        return False
    try:
        return SOURCE.is_dir()
    except OSError:
        return False


if _production_run_command():
    start_daily_backup_thread()
