from __future__ import annotations

"""Start and supervise the independent SiBot 1 SHADOW/PAPER controller.

This sidecar has no signer/private-key input and cannot enable LIVE execution.
It runs only for the production `learnerbot run` command, starts after MAIN BOOT
has had time to finish its audited startup composition, and publishes only a
sanitised runner-readable health snapshot under /var/tmp/boot.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_STARTED = False
_THREAD: threading.Thread | None = None
_CHILD: subprocess.Popen | None = None
_SUPERVISOR_STATUS = Path("/var/tmp/boot/sibot1_shadow_supervisor.json")
_PUBLIC_STATUS = Path("/var/tmp/boot/sibot1_shadow_status.json")
_ENGINE_IDS = ("gpt", "gemini", "grok")
_RUNTIME_FRESH_SECONDS = 20


def _runtime_command() -> bool:
    return len(sys.argv) >= 2 and str(sys.argv[1]).strip().lower() == "run"


def _enabled() -> bool:
    return str(os.environ.get("SIBOT1_SHADOW_AUTOSTART", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _write_supervisor_status(state: str, detail: str = "", child_pid: int | None = None) -> None:
    try:
        _atomic_json(
            _SUPERVISOR_STATUS,
            {
                "schema_version": 1,
                "state": str(state),
                "detail": str(detail or "")[:500],
                "parent_pid": os.getpid(),
                "child_pid": child_pid,
                "mode": "SHADOW",
                "live_enabled": False,
                "signer_attached": False,
                "broadcast_enabled": False,
                "wallet_private_key_access": False,
                "updated_epoch": int(time.time()),
            },
        )
    except Exception:
        pass


def _public_workers(runtime: dict[str, Any], *, fresh: bool) -> dict[str, dict[str, Any]]:
    source = runtime.get("workers") if isinstance(runtime.get("workers"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for engine_id in _ENGINE_IDS:
        row = source.get(engine_id) if isinstance(source, dict) else None
        if not isinstance(row, dict) or not fresh:
            out[engine_id] = {"state": "UNKNOWN", "pid": None, "alive": False}
            continue
        state = str(row.get("state") or "UNKNOWN").upper()
        pid = row.get("pid")
        try:
            pid = int(pid) if pid else None
        except Exception:
            pid = None
        out[engine_id] = {
            "state": state,
            "pid": pid,
            "alive": bool(row.get("alive") is True),
        }
    return out


def _publish_public_status(root: Path, supervisor_state: str, child_pid: int | None) -> None:
    now = int(time.time())
    runtime_path = root / "data" / "sibot1" / "status.json"
    runtime: dict[str, Any] = {}
    try:
        loaded = json.loads(runtime_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            runtime = loaded
    except Exception:
        runtime = {}
    try:
        runtime_updated = int(runtime.get("updated_epoch") or 0)
    except Exception:
        runtime_updated = 0
    fresh = runtime_updated > 0 and 0 <= now - runtime_updated <= _RUNTIME_FRESH_SECONDS
    mode = str(runtime.get("mode") or "SHADOW").upper() if fresh else "SHADOW"
    safe_boundary = (
        mode in {"SHADOW", "PAPER"}
        and runtime.get("live_enabled") is False
        and runtime.get("signer_attached") is False
        and runtime.get("broadcast_enabled") is False
        and runtime.get("wallet_private_key_access") is False
    ) if fresh else False
    workers = _public_workers(runtime, fresh=fresh)
    runtime_active = fresh and all(
        workers[e]["alive"] is True and workers[e]["state"] in {"READY", "HEALTH"}
        for e in _ENGINE_IDS
    )
    public_state = "ACTIVE" if supervisor_state == "ACTIVE" and runtime_active and safe_boundary else supervisor_state
    if supervisor_state == "ACTIVE" and public_state == "ACTIVE" and not safe_boundary:
        public_state = "FAILED_SAFE_BOUNDARY"
    payload = {
        "schema_version": 1,
        "supervisor_state": str(public_state),
        "mode": mode,
        "live_enabled": False,
        "signer_attached": False,
        "broadcast_enabled": False,
        "wallet_private_key_access": False,
        "child_pid": child_pid,
        "updated_epoch": now,
        "runtime_updated_epoch": runtime_updated,
        "runtime_fresh": bool(fresh),
        "workers": workers,
        "open_lots": int(runtime.get("open_lots") or 0) if fresh else 0,
    }
    try:
        _atomic_json(_PUBLIC_STATUS, payload)
    except Exception:
        pass


def _supervise() -> None:
    global _CHILD
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "sibot1_shadow_runtime.py"
    # Keep the independent child away from MAIN BOOT startup composition. The
    # sidecar only reads shared evidence and cannot patch audited trading hooks.
    time.sleep(15)
    while True:
        try:
            env = dict(os.environ)
            env["SIBOT1_EXECUTION_MODE"] = "SHADOW"
            _CHILD = subprocess.Popen(
                [sys.executable, str(script), "--root", str(root)],
                cwd=str(root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=None,
                stderr=None,
                close_fds=True,
            )
            _write_supervisor_status("ACTIVE", "SiBot1 SHADOW sidecar started", _CHILD.pid)
            print(
                f"[sibot1-shadow] controller active pid={_CHILD.pid} live=false signer=false broadcast=false",
                flush=True,
            )
            while _CHILD.poll() is None:
                _publish_public_status(root, "ACTIVE", _CHILD.pid)
                time.sleep(2)
            rc = int(_CHILD.returncode or 0)
            _write_supervisor_status("RESTARTING", f"sidecar exited rc={rc}; restart scheduled", _CHILD.pid)
            _publish_public_status(root, "RESTARTING", _CHILD.pid)
            print(f"[sibot1-shadow] sidecar exited rc={rc}; restarting", flush=True)
        except Exception as exc:
            _write_supervisor_status("FAILED", f"{type(exc).__name__}: {exc}")
            _publish_public_status(root, "FAILED", None)
            print(f"[sibot1-shadow] supervisor error {type(exc).__name__}: {exc}", flush=True)
        time.sleep(10)


def install() -> None:
    global _STARTED, _THREAD
    if _STARTED:
        return
    _STARTED = True
    if not _runtime_command():
        return
    if not _enabled():
        _write_supervisor_status("DISABLED", "SIBOT1_SHADOW_AUTOSTART disabled")
        try:
            _publish_public_status(Path(__file__).resolve().parents[1], "DISABLED", None)
        except Exception:
            pass
        return
    _THREAD = threading.Thread(target=_supervise, name="sibot1-shadow-supervisor", daemon=True)
    _THREAD.start()
    _write_supervisor_status("STARTING", "supervisor thread launched")
    try:
        _publish_public_status(Path(__file__).resolve().parents[1], "STARTING", None)
    except Exception:
        pass


install()
