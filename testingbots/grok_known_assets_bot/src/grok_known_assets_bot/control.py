from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_CONTROL_PATH = "/home/ayman01323/BOOT/testingbots/grok_known_assets_bot/grok_control.json"


def control_path() -> Path:
    return Path(os.environ.get("GROK_CONTROL_FILE", DEFAULT_CONTROL_PATH)).expanduser()


def default_state() -> dict[str, Any]:
    return {
        "armed": False,
        "mode": "PAPER_ONLY",
        "live_readiness_enabled": False,
        "live_money_enabled": False,
        "updated_epoch": 0,
        "updated_by": "",
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or control_path()
    state = default_state()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state.update(raw)
    except FileNotFoundError:
        pass
    except Exception:
        # Fail closed if the control file is malformed or unreadable.
        return default_state()

    state["armed"] = bool(state.get("armed", False))
    state["live_readiness_enabled"] = bool(state.get("live_readiness_enabled", False))
    state["mode"] = "LIVE_READINESS" if state["live_readiness_enabled"] else "PAPER_ONLY"
    # Hard boundary: this component never signs or broadcasts money transactions.
    state["live_money_enabled"] = False
    return state


def save_state(
    *,
    armed: bool,
    live_readiness_enabled: bool | None = None,
    updated_by: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    target = path or control_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = load_state(target)
    readiness = previous.get("live_readiness_enabled", False) if live_readiness_enabled is None else bool(live_readiness_enabled)
    state = {
        "armed": bool(armed),
        "mode": "LIVE_READINESS" if readiness else "PAPER_ONLY",
        "live_readiness_enabled": readiness,
        "live_money_enabled": False,
        "updated_epoch": int(time.time()),
        "updated_by": str(updated_by or ""),
    }

    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
    return state


def is_armed(path: Path | None = None) -> bool:
    return bool(load_state(path).get("armed", False))


def is_live_readiness_enabled(path: Path | None = None) -> bool:
    state = load_state(path)
    return bool(state.get("armed") and state.get("live_readiness_enabled"))
