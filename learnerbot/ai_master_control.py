from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

PROVIDERS = ("auto", "gpt", "gemini", "copilot", "claude", "deepseek")
LANES = ("strategy", "engineering")
CYCLES = ("scheduled", "manual")
VPS_ACTIONS = ("none", "inspect", "test", "deploy")
DEEPSEEK_GITHUB_ACTIONS = ("none", "inspect", "test", "draft_fix")
DEEPSEEK_VPS_ACTIONS = ("none", "inspect", "test", "deploy")

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
    "claude_vps_action": "none",
    "claude_vps_action_nonce": 0,
    "claude_vps_last_request_epoch": 0,
    "deepseek_github_action": "none",
    "deepseek_github_action_nonce": 0,
    "deepseek_github_task": "",
    "deepseek_github_last_request_epoch": 0,
    "deepseek_vps_action": "none",
    "deepseek_vps_action_nonce": 0,
    "deepseek_vps_last_request_epoch": 0,
    "updated_epoch": 0,
    "updated_by": "",
}


def _path(app) -> Path:
    return Path(app.data_dir) / "ai_master_control.json"


def bridge_path() -> Path:
    return Path("/var/tmp/boot/ai_master_control.json")


def vps_result_path() -> Path:
    return Path("/var/tmp/boot/claude_vps_ops_latest.json")


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _task(value: Any) -> str:
    # Telegram tasks are operator instructions, never credentials. Keep the bridge
    # compact and remove NULs/control padding before GitHub workflow dispatch.
    return str(value or "").replace("\x00", "").strip()[:800]


def sanitise(raw: dict | None) -> dict:
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_CONTROL)
    for lane in LANES:
        master = str(src.get(f"{lane}_master") or out[f"{lane}_master"]).lower().strip()
        out[f"{lane}_master"] = master if master in PROVIDERS else "auto"
        cycle = str(src.get(f"{lane}_cycle") or out[f"{lane}_cycle"]).lower().strip()
        out[f"{lane}_cycle"] = cycle if cycle in CYCLES else "scheduled"
        out[f"{lane}_enabled"] = _bool(src.get(f"{lane}_enabled"), True)
        out[f"{lane}_run_nonce"] = _nonnegative_int(src.get(f"{lane}_run_nonce"))

    action = str(src.get("claude_vps_action") or "none").lower().strip()
    out["claude_vps_action"] = action if action in VPS_ACTIONS else "none"
    out["claude_vps_action_nonce"] = _nonnegative_int(src.get("claude_vps_action_nonce"))
    out["claude_vps_last_request_epoch"] = _nonnegative_int(src.get("claude_vps_last_request_epoch"))

    ds_gh = str(src.get("deepseek_github_action") or "none").lower().strip()
    out["deepseek_github_action"] = ds_gh if ds_gh in DEEPSEEK_GITHUB_ACTIONS else "none"
    out["deepseek_github_action_nonce"] = _nonnegative_int(src.get("deepseek_github_action_nonce"))
    out["deepseek_github_task"] = _task(src.get("deepseek_github_task"))
    out["deepseek_github_last_request_epoch"] = _nonnegative_int(src.get("deepseek_github_last_request_epoch"))

    ds_vps = str(src.get("deepseek_vps_action") or "none").lower().strip()
    out["deepseek_vps_action"] = ds_vps if ds_vps in DEEPSEEK_VPS_ACTIONS else "none"
    out["deepseek_vps_action_nonce"] = _nonnegative_int(src.get("deepseek_vps_action_nonce"))
    out["deepseek_vps_last_request_epoch"] = _nonnegative_int(src.get("deepseek_vps_last_request_epoch"))

    out["updated_epoch"] = _nonnegative_int(src.get("updated_epoch"))
    out["updated_by"] = str(src.get("updated_by") or "")[:80]
    return out


def load(app) -> dict:
    try:
        raw = json.loads(_path(app).read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    return sanitise(raw)


def load_vps_result() -> dict:
    try:
        value = json.loads(vps_result_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


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
    # The trading process gets no GitHub credential. A self-hosted Actions job
    # reads this sanitised bridge and publishes/dispatches only bounded requests.
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


def request_vps_action(app, action: str, *, updated_by: str | int = "") -> dict:
    action = str(action).lower().strip()
    if action not in VPS_ACTIONS or action == "none":
        raise ValueError("unsupported Claude VPS action")
    value = load(app)
    value["claude_vps_action"] = action
    value["claude_vps_action_nonce"] = int(value.get("claude_vps_action_nonce") or 0) + 1
    value["claude_vps_last_request_epoch"] = int(time.time())
    return save(app, value, updated_by=updated_by)


def request_deepseek_github_action(
    app,
    action: str,
    *,
    task: str = "",
    updated_by: str | int = "",
) -> dict:
    action = str(action).lower().strip()
    if action not in DEEPSEEK_GITHUB_ACTIONS or action == "none":
        raise ValueError("unsupported DeepSeek GitHub action")
    clean_task = _task(task)
    if action == "draft_fix" and not clean_task:
        raise ValueError("DeepSeek draft_fix requires a task")
    value = load(app)
    value["deepseek_github_action"] = action
    value["deepseek_github_action_nonce"] = int(value.get("deepseek_github_action_nonce") or 0) + 1
    value["deepseek_github_task"] = clean_task
    value["deepseek_github_last_request_epoch"] = int(time.time())
    return save(app, value, updated_by=updated_by)


def request_deepseek_vps_action(app, action: str, *, updated_by: str | int = "") -> dict:
    action = str(action).lower().strip()
    if action not in DEEPSEEK_VPS_ACTIONS or action == "none":
        raise ValueError("unsupported DeepSeek VPS action")
    value = load(app)
    value["deepseek_vps_action"] = action
    value["deepseek_vps_action_nonce"] = int(value.get("deepseek_vps_action_nonce") or 0) + 1
    value["deepseek_vps_last_request_epoch"] = int(time.time())
    return save(app, value, updated_by=updated_by)


# Install the multi-agent AI health/reporting layer when the MASTER control is loaded.
from . import ai_four_agent_health_patch  # noqa: E402,F401
