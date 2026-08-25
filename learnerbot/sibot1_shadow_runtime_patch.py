from __future__ import annotations

"""Start the SiBot 1 SHADOW controller as a supervised child process.

This sidecar is intentionally paper-only. It has no signer/private-key input and
cannot enable LIVE execution. It runs only for the production `learnerbot run`
command and is kept independent from the existing MAIN BOOT trading loops.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_STARTED = False
_THREAD: threading.Thread | None = None
_CHILD: subprocess.Popen | None = None
_STATUS = Path("/var/tmp/boot/sibot1_shadow_supervisor.json")


def _runtime_command() -> bool:
    return len(sys.argv) >= 2 and str(sys.argv[1]).strip().lower() == "run"


def _enabled() -> bool:
    return str(os.environ.get("SIBOT1_SHADOW_AUTOSTART", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _write_status(state: str, detail: str = "", child_pid: int | None = None) -> None:
    try:
        _STATUS.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "state": str(state),
            "detail": str(detail or "")[:500],
            "parent_pid": os.getpid(),
            "child_pid": child_pid,
            "mode": "SHADOW",
            "live_enabled": False,
            "signer_attached": False,
            "broadcast_enabled": False,
            "updated_epoch": int(time.time()),
        }
        tmp = _STATUS.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, _STATUS)
    except Exception:
        pass


def _supervise() -> None:
    global _CHILD
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "sibot1_shadow_runtime.py"
    # Let final_runtime_integrity_patch finish before the sidecar begins reading
    # shared evidence. The sidecar never patches audited trading hooks.
    time.sleep(5)
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
            _write_status("ACTIVE", "SiBot1 SHADOW sidecar started", _CHILD.pid)
            print(f"[sibot1-shadow] controller active pid={_CHILD.pid} live=false signer=false broadcast=false", flush=True)
            rc = _CHILD.wait()
            _write_status("RESTARTING", f"sidecar exited rc={rc}; restart scheduled", _CHILD.pid)
            print(f"[sibot1-shadow] sidecar exited rc={rc}; restarting", flush=True)
        except Exception as exc:
            _write_status("FAILED", f"{type(exc).__name__}: {exc}")
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
        _write_status("DISABLED", "SIBOT1_SHADOW_AUTOSTART disabled")
        return
    _THREAD = threading.Thread(target=_supervise, name="sibot1-shadow-supervisor", daemon=True)
    _THREAD.start()
    _write_status("STARTING", "supervisor thread launched")


install()
