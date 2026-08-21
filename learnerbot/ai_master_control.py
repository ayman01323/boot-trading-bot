from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

PROVIDERS = ("auto", "gpt", "gemini", "copilot", "claude")
LANES = ("strategy", "engineering")
CYCLES = ("scheduled", "manual")

DEFAULT_CONTROL = {
    "schema_version": 1,
    "strategy_master": "auto",
    "engineering_master": "auto",
    "strategy_cycle": "scheduled",
    "engineering_cycle": "scheduled",
    "strategy_enabled": True,
    "engineering_enabled": True,
    "strategy_run_nonce": 0,
    "engineering_run_nonce": 0,
    "updated_epoch": 0,
    "updated_by": "",
}


def _path(app) -> Path:
    return Path(app.data_dir) / "ai_master_control.json"


def bridge_path() -> Path:
    return Path("/var/tmp/boot/ai_master_control.json")


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def sanitise(raw: dict | None) -> dict:
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_CONTROL)
    for lane in LANES:
        master = str(src.get(f"{lane}_master") or out[f"{lane}_master"]).lower().strip()
        out[f"{lane}_master"] = master if master in PROVIDERS else "auto"
        cycle = str(src.get(f"{lane}_cycle") or out[f"{lane}_cycle"]).lower().strip()
        out[f"{lane}_cycle"] = cycle if cycle in CYCLES else "scheduled"
        out[f"{lane}_enabled"] = _bool(src.get(f"{lane}_enabled"), True)
        try:
            out[f"{lane}_run_nonce"] = max(0, int(src.get(f"{lane}_run_nonce") or 0))
        except Exception:
            out[f"{lane}_run_nonce"] = 0
    try:
        out["updated_epoch"] = max(0, int(src.get("updated_epoch") or 0))
    except Exception:
        out["updated_epoch"] = 0
    out["updated_by"] = str(src.get("updated_by") or "")[:80]
    return out


def load(app) -> dict:
    try:
        raw = json.loads(_path(app).read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    return sanitise(raw)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save(app, value: dict, *, updated_by: str | int = "") -> dict:
    out = sanitise(value)
    out["updated_epoch"] = int(time.time())
    out["updated_by"] = str(updated_by or "")[:80]
    _atomic_json(_path(app), out)
    # The trading process gets no GitHub credential.  A self-hosted Actions job
    # reads this sanitised bridge and publishes it to ai-reviews.
    try:
        _atomic_json(bridge_path(), out)
        os.chmod(bridge_path(), 0o644)
    except Exception as exc:
        print(f"[ai-master-control-bridge] {type(exc).__name__}: {exc}")
    return out


def set_master(app, lane: str, provider: str, *, updated_by: str | int = "") -> dict:
    lane = str(lane).lower().strip()
    provider = str(provider).lower().strip()
    if lane not in LANES:
        raise ValueError("unsupported AI lane")
    if provider not in PROVIDERS:
        raise ValueError("unsupported master AI provider")
    value = load(app)
    value[f"{lane}_master"] = provider
    return save(app, value, updated_by=updated_by)


def set_cycle(app, lane: str, cycle: str, *, updated_by: str | int = "") -> dict:
    lane = str(lane).lower().strip()
    cycle = str(cycle).lower().strip()
    if lane not in LANES:
        raise ValueError("unsupported AI lane")
    if cycle not in CYCLES:
        raise ValueError("unsupported cycle mode")
    value = load(app)
    value[f"{lane}_cycle"] = cycle
    value[f"{lane}_enabled"] = True
    return save(app, value, updated_by=updated_by)


def set_enabled(app, lane: str, enabled: bool, *, updated_by: str | int = "") -> dict:
    lane = str(lane).lower().strip()
    if lane not in LANES:
        raise ValueError("unsupported AI lane")
    value = load(app)
    value[f"{lane}_enabled"] = bool(enabled)
    return save(app, value, updated_by=updated_by)


def request_run(app, lane: str, *, updated_by: str | int = "") -> dict:
    lane = str(lane).lower().strip()
    if lane not in LANES:
        raise ValueError("unsupported AI lane")
    value = load(app)
    value[f"{lane}_enabled"] = True
    value[f"{lane}_run_nonce"] = int(value.get(f"{lane}_run_nonce") or 0) + 1
    return save(app, value, updated_by=updated_by)
