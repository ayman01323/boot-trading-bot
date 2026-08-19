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
DRIVE_DEST = "gdrive:BotBuc"
RETENTION_DAYS = 30
DAILY_HOUR_LOCAL = 3
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
    """Create one atomic ZIP containing the complete bot folder tree.

    Regular files, empty directories and symlinks are retained. Runtime special
    files such as sockets/FIFOs are skipped because ZIP has no portable encoding
    for them. The archive is written beside the final path then atomically renamed.
    """
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

            # Preserve the directory itself, including empty directories.
            root_arc = root_path.relative_to(source_parent).as_posix().rstrip("/") + "/"
            try:
                zf.write(root_path, root_arc)
            except (FileNotFoundError, PermissionError, OSError):
                skipped += 1

            # Preserve symlinked directories without traversing through them.
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
                    # A live runtime file may disappear between os.walk and read.
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


def cleanup_local_backups(today: date | None = None) -> list[Path]:
    """Delete only SERVER ZIPs older than 30 days; never touches Google Drive."""
    _ensure_backup_dir()
    today = today or date.today()
    cutoff = today - timedelta(days=RETENTION_DAYS)
    removed: list[Path] = []
    for path in sorted(BACKUP_DIR.glob("*.zip")):
        archive_day = _date_from_archive(path)
        if archive_day is not None and archive_day < cutoff:
            try:
                path.unlink()
                removed.append(path)
                _log(f"deleted local backup older than {RETENTION_DAYS} days: {path.name}")
            except FileNotFoundError:
                pass
    return removed


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
    if "gdrive:" not in remotes:
        return False, "rclone config does not contain gdrive:"
    return True, exe


def upload_to_drive(archive: Path) -> bool:
    """Upload/replace today's ZIP in Google Drive BotBuc. No remote retention deletion."""
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


def run_daily_backup(today: date | None = None) -> Path:
    """Ensure today's archive exists, retain 30 days locally, then upload to Drive."""
    _ensure_backup_dir()
    today = today or date.today()
    target = BACKUP_DIR / f"{today.isoformat()}.zip"
    if not target.is_file() or target.stat().st_size <= 0:
        _build_zip(target)
    else:
        _log(f"today's local backup already exists: {target.name} bytes={target.stat().st_size}")

    # User instruction: retention applies ONLY on the server.
    cleanup_local_backups(today)

    # Drive copies are intentionally never deleted by this worker.
    upload_to_drive(target)
    return target


def _seconds_until_next_daily_run(now: datetime | None = None) -> float:
    now = now or datetime.now()
    target = now.replace(hour=DAILY_HOUR_LOCAL, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


def _worker() -> None:
    # Let the run command obtain its singleton lock and finish startup first.
    time.sleep(_INITIAL_DELAY_SECONDS)
    while True:
        try:
            run_daily_backup()
        except Exception as exc:
            _log(f"backup failed: {type(exc).__name__}: {exc}")
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
            f"daily={DAILY_HOUR_LOCAL:02d}:00 local retention_server_days={RETENTION_DAYS} drive_retention=unlimited"
        )
        return thread


def _production_run_command() -> bool:
    # Inspect argv first so normal imports/tests never even probe the protected /root path.
    args = sys.argv[1:]
    if not (args and args[0] == "run"):
        return False
    try:
        return SOURCE.is_dir()
    except OSError:
        return False


if _production_run_command():
    start_daily_backup_thread()
