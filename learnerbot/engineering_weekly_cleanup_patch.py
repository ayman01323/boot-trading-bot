from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import cli as _cli
from . import daily_botbuc_backup_patch as _backup
from . import monitor_factory_pipeline as _pipeline
from . import report_schedule_control as _sched

WEEK_SECONDS = 7 * 24 * 60 * 60
INITIAL_DELAY_SECONDS = 90
CHECK_SECONDS = 60 * 60
LOW_FREE_BYTES = 3 * 1024**3
BACKUP_MIN_FREE_BYTES = 4 * 1024**3
PRODUCTION_BACKUP_DIR = Path("/root/BotBuc")
STALE_TMP_SECONDS = 24 * 60 * 60
STALE_BACKUP_TMP_SECONDS = 12 * 60 * 60
RUNNER_DIAG_RETENTION_SECONDS = 7 * 24 * 60 * 60
PIP_CACHE_RETENTION_SECONDS = 7 * 24 * 60 * 60

TMP_DIR_PREFIXES = (
    "deepseek-telegram-bridge-ci-",
    "ai-mailbox-telegram-ci-",
    "ai-mailbox-provider-",
    "five-agent-recovery-",
    "weekly-audit-ci-",
    "boot-failed-test-diagnosis-",
    "selected-ai-master-ci-",
    "pip-unpack-",
    "pip-install-",
    "pip-ephem-wheel-cache-",
    "pip-req-tracker-",
)

_STARTED = False
_LOCK = threading.Lock()
_PREV_APP = _cli._app
_ORIGINAL_BUILD_ZIP = _backup._build_zip


def _log(message: str) -> None:
    print(f"[engineering-weekly-cleanup] {message}", flush=True)


def _result_path(app) -> Path:
    path = Path(app.data_dir) / "monitor_factory" / "engineering_weekly_cleanup_latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_result(app) -> dict[str, Any]:
    try:
        data = json.loads(_result_path(app).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_result(app, payload: dict[str, Any]) -> None:
    path = _result_path(app)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _disk(path: Path | str = "/") -> dict[str, int | float]:
    usage = shutil.disk_usage(path)
    used_pct = round((usage.used / usage.total * 100.0), 2) if usage.total else 0.0
    return {
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "used_pct": used_pct,
    }


def _age_seconds(path: Path, now: float) -> float:
    try:
        return max(0.0, now - path.stat().st_mtime)
    except OSError:
        return 0.0


def _path_busy(path: Path) -> bool | None:
    """True if fuser proves the path is open, False if it proves no user, None if unavailable."""
    exe = shutil.which("fuser")
    if not exe:
        return None
    try:
        cp = subprocess.run(
            [exe, "-s", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    if cp.returncode == 0:
        return True
    if cp.returncode == 1:
        return False
    return None


def _remove_tree(path: Path, removed: list[dict], errors: list[dict]) -> None:
    try:
        size = 0
        for root, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    size += (Path(root) / name).stat().st_size
                except OSError:
                    pass
        shutil.rmtree(path)
        removed.append({"path": str(path), "kind": "dir", "bytes_estimate": int(size)})
    except FileNotFoundError:
        return
    except Exception as exc:
        errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"[:500]})


def _remove_file(path: Path, removed: list[dict], errors: list[dict]) -> None:
    try:
        size = int(path.stat().st_size)
        path.unlink()
        removed.append({"path": str(path), "kind": "file", "bytes_estimate": size})
    except FileNotFoundError:
        return
    except Exception as exc:
        errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"[:500]})


def _cleanup_tmp(root: Path, now: float, removed: list[dict], errors: list[dict]) -> None:
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        if not path.name.startswith(TMP_DIR_PREFIXES):
            continue
        if _age_seconds(path, now) < STALE_TMP_SECONDS:
            continue
        _remove_tree(path, removed, errors)


def _cleanup_runner_diag(root: Path, now: float, removed: list[dict], errors: list[dict]) -> None:
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if _age_seconds(path, now) < RUNNER_DIAG_RETENTION_SECONDS:
            continue
        _remove_file(path, removed, errors)


def _cleanup_runner_temp(root: Path, now: float, removed: list[dict], errors: list[dict]) -> None:
    if not root.is_dir():
        return
    for path in list(root.iterdir()):
        if _age_seconds(path, now) < STALE_TMP_SECONDS:
            continue
        if path.is_symlink() or path.is_file():
            _remove_file(path, removed, errors)
        elif path.is_dir():
            _remove_tree(path, removed, errors)


def _cleanup_pip_cache(root: Path, now: float, removed: list[dict], errors: list[dict]) -> None:
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if _age_seconds(path, now) < PIP_CACHE_RETENTION_SECONDS:
            continue
        _remove_file(path, removed, errors)


def _cleanup_backup_tmp(root: Path, now: float, removed: list[dict], skipped: list[dict], errors: list[dict]) -> None:
    if not root.is_dir():
        return
    for path in root.glob(".*.zip.tmp"):
        if not path.is_file():
            continue
        age = _age_seconds(path, now)
        if age < STALE_BACKUP_TMP_SECONDS:
            skipped.append({"path": str(path), "reason": "backup temp is too recent", "age_seconds": int(age)})
            continue
        busy = _path_busy(path)
        if busy is not False:
            skipped.append({
                "path": str(path),
                "reason": "backup temp is open or open-file state could not be proven safe",
                "busy": busy,
            })
            continue
        _remove_file(path, removed, errors)


def _roots(overrides: dict[str, Path] | None = None) -> dict[str, Path]:
    roots = {
        "tmp": Path("/tmp"),
        "runner_diag": Path("/opt/actions-runner/_diag"),
        "runner_temp": Path("/opt/actions-runner/_work/_temp"),
        "pip_cache": Path("/root/.cache/pip"),
        "backup": _backup.BACKUP_DIR,
    }
    if overrides:
        roots.update({str(k): Path(v) for k, v in overrides.items()})
    return roots


def run_weekly_cleanup(app, *, now: float | None = None, roots: dict[str, Path] | None = None) -> dict[str, Any]:
    now = float(now if now is not None else time.time())
    before = _disk("/")
    removed: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    rs = _roots(roots)

    _cleanup_tmp(rs["tmp"], now, removed, errors)
    _cleanup_runner_diag(rs["runner_diag"], now, removed, errors)
    _cleanup_runner_temp(rs["runner_temp"], now, removed, errors)
    _cleanup_pip_cache(rs["pip_cache"], now, removed, errors)
    _cleanup_backup_tmp(rs["backup"], now, removed, skipped, errors)

    after = _disk("/")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "mode": "ENGINEERING_WEEKLY_CLEANUP",
        "generated_epoch": int(now),
        "interval_hours": 168,
        "before": before,
        "after": after,
        "bytes_recovered": max(0, int(after["free_bytes"]) - int(before["free_bytes"])),
        "removed": removed,
        "skipped": skipped,
        "errors": errors,
        "safety": {
            "deletes_databases": False,
            "deletes_sqlite_wal": False,
            "deletes_csvbot": False,
            "deletes_wallets_or_keys": False,
            "deletes_env_or_config": False,
            "deletes_completed_backups": False,
            "allowlisted_temp_cache_diag_only": True,
        },
        "status": "SUCCESS" if not errors else "PARTIAL",
    }
    _write_result(app, payload)

    if int(after["free_bytes"]) < LOW_FREE_BYTES:
        finding = _pipeline.record_finding(
            app,
            lane="ENGINEERING",
            finding_type="PROBLEM",
            classification="INFRASTRUCTURE",
            severity="P1" if int(after["free_bytes"]) < 1024**3 else "P2",
            title="Server disk free space remains below Engineering Monitor safety headroom",
            scope="WEEKLY_DISK_MAINTENANCE",
            source_version=time.strftime("%Y-%m-%d", time.gmtime(now)),
            evidence={
                "cleanup": payload,
                "required_free_bytes": LOW_FREE_BYTES,
                "backup_min_free_bytes": BACKUP_MIN_FREE_BYTES,
            },
            recommendation=(
                "Inspect growth in SQLite/derived exports/logs and expand or rebalance storage. "
                "Do not delete live databases, WAL files, wallets, configuration or trading evidence."
            ),
            acceptance_test=f"Root filesystem free space must be at least {LOW_FREE_BYTES} bytes after safe maintenance.",
            now=int(now),
        )
        _pipeline.queue_finding(app, finding, now=int(now))
        payload["engineering_finding"] = finding
        _write_result(app, payload)

    _log(
        f"status={payload['status']} removed={len(removed)} errors={len(errors)} "
        f"recovered_bytes={payload['bytes_recovered']} free_bytes={after['free_bytes']}"
    )
    return payload


def _is_production_backup_target(target: Path) -> bool:
    """Apply the VPS headroom policy only to the real /root/BotBuc archive path."""
    try:
        backup_dir = Path(_backup.BACKUP_DIR).resolve(strict=False)
        target_parent = Path(target).parent.resolve(strict=False)
        production_dir = PRODUCTION_BACKUP_DIR.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    return backup_dir == production_dir and target_parent == production_dir


def _backup_build_with_headroom(target: Path) -> None:
    """Fail before a production multi-GB ZIP when the root filesystem lacks headroom."""
    if not _is_production_backup_target(target):
        return _ORIGINAL_BUILD_ZIP(target)

    _backup._ensure_backup_dir()
    now = time.time()
    tmp = _backup.BACKUP_DIR / f".{target.name}.tmp"

    if tmp.exists() and _age_seconds(tmp, now) >= STALE_BACKUP_TMP_SECONDS:
        busy = _path_busy(tmp)
        if busy is False:
            try:
                tmp.unlink()
                _log(f"removed stale incomplete backup before retry: {tmp}")
            except OSError as exc:
                raise RuntimeError(f"could not remove stale backup temp {tmp}: {exc}") from exc

    free = int(shutil.disk_usage(_backup.BACKUP_DIR).free)
    if free < BACKUP_MIN_FREE_BYTES:
        raise RuntimeError(
            "backup preflight refused: insufficient disk headroom "
            f"free_bytes={free} required_bytes={BACKUP_MIN_FREE_BYTES}; "
            "no temporary ZIP was started"
        )
    return _ORIGINAL_BUILD_ZIP(target)


def install_backup_headroom_guard() -> None:
    if getattr(_backup, "_engineering_backup_headroom_guard_installed", False):
        return
    _backup._build_zip = _backup_build_with_headroom
    _backup._engineering_backup_headroom_guard_installed = True
    _log(f"backup preflight installed production_dir={PRODUCTION_BACKUP_DIR} min_free_bytes={BACKUP_MIN_FREE_BYTES}")


def _due(app, now: float | None = None) -> bool:
    now = float(now if now is not None else time.time())
    last = _read_result(app)
    try:
        last_epoch = int(last.get("generated_epoch") or 0)
    except Exception:
        last_epoch = 0
    return last_epoch <= 0 or now - last_epoch >= WEEK_SECONDS


def _worker(app) -> None:
    time.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            if _due(app):
                run_weekly_cleanup(app)
        except Exception as exc:
            _log(f"ERROR {type(exc).__name__}: {exc}")
        time.sleep(CHECK_SECONDS)


def start_weekly_cleanup_thread(app) -> threading.Thread | None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return None
        thread = threading.Thread(
            target=_worker,
            args=(app,),
            name="engineering-weekly-cleanup",
            daemon=True,
        )
        thread.start()
        _STARTED = True
        _log("enabled interval=168h first_run_if_no_history=true")
        return thread


def _app_with_weekly_cleanup():
    app = _PREV_APP()
    start_weekly_cleanup_thread(app)
    return app


install_backup_headroom_guard()

# Surface the maintenance responsibility under the existing Engineering Monitor label.
try:
    meta = _sched.REPORTS.get("engineering") or {}
    description = str(meta.get("description") or "")
    note = " Weekly self-cleaning removes only allow-listed stale temp/cache/runner diagnostics and reports disk headroom."
    if note.strip() not in description:
        meta["description"] = (description + note).strip()
except Exception:
    pass

_cli._app = _app_with_weekly_cleanup
