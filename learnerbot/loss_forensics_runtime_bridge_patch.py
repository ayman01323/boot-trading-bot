from __future__ import annotations

import json
import os
from pathlib import Path

from . import claude_rpc_runtime_bridge_patch as _claude_rpc_bridge  # noqa: F401
from . import loss_forensics_github_export as _export
from . import transaction_audit_worker_patch as _worker

# The bot service can read the private runtime databases but may intentionally use
# a read-only Git deploy key.  The self-hosted GitHub runner, by contrast, has a
# short-lived repository token but must not be given wallet/database access.  This
# file is the narrow, sanitised hand-off between those two trust domains.
DEFAULT_BRIDGE_PATH = Path("/var/tmp/boot/latest_loss_forensics.json")
BRIDGE_PATH = Path(os.getenv("BOOT_FORENSICS_BRIDGE_PATH", str(DEFAULT_BRIDGE_PATH)))
_PREV_PUBLISH = _worker.publish_loss_forensics


def _atomic_write_report(report: dict, path: Path | None = None) -> Path:
    target = Path(path or BRIDGE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)
    return target


def publish_loss_forensics_with_runtime_bridge(app, zip_path, gpt_result=None) -> dict:
    """Preserve existing best-effort Git publishing and always expose a safe local copy.

    The report is already sanitised by ``build_loss_forensics``.  Writing the same
    object to a world-readable /var/tmp bridge lets the self-hosted Actions runner
    publish it with GitHub's ephemeral token, fixing stale/missing Strategy Lab
    evidence without giving the trading service a long-lived GitHub credential.
    """
    result = _PREV_PUBLISH(app, zip_path, gpt_result)
    report = result.get("report") if isinstance(result, dict) else None
    if isinstance(report, dict) and int(report.get("generated_epoch") or 0) > 0:
        try:
            path = _atomic_write_report(report)
            result = dict(result)
            result["runtime_bridge"] = str(path)
            result["runtime_bridge_ok"] = True
        except Exception as exc:
            result = dict(result)
            result["runtime_bridge_ok"] = False
            result["runtime_bridge_error"] = f"{type(exc).__name__}: {exc}"[:800]
    return result


def install() -> None:
    if getattr(_worker, "_loss_forensics_runtime_bridge_installed", False):
        return
    # transaction_audit_worker_patch imported the function by name, so patch both
    # the defining module and the worker's already-bound reference.
    _export.publish_loss_forensics = publish_loss_forensics_with_runtime_bridge
    _worker.publish_loss_forensics = publish_loss_forensics_with_runtime_bridge
    _worker._loss_forensics_runtime_bridge_installed = True


install()
