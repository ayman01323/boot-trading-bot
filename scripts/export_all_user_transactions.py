#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# When this file is executed directly, Python places ``scripts/`` rather than the
# repository root on sys.path. Add the project root explicitly so the sibling
# ``learnerbot`` package resolves from an ordinary VPS shell invocation.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The production service/deployer intentionally runs the bot from .venv. A shell
# command such as ``python3 scripts/...`` may otherwise use the system interpreter
# and miss required packages. Transparently re-exec with the production Python.
_VENV_PYTHON = _REPO_ROOT / ".venv" / "bin" / "python"
if (
    _VENV_PYTHON.exists()
    and os.environ.get("BOOT_AUDIT_VENV_REEXEC") != "1"
    and Path(sys.executable).resolve() != _VENV_PYTHON.resolve()
):
    env = dict(os.environ)
    env["BOOT_AUDIT_VENV_REEXEC"] = "1"
    os.execve(
        str(_VENV_PYTHON),
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        env,
    )

from learnerbot.config import AppSettings
from learnerbot.transaction_audit import run_transaction_audit
from learnerbot import transaction_audit_worker_patch as _worker
from learnerbot import hourly_gpt_live_engine_wording_patch  # noqa: F401
from learnerbot import profit_control_loop_patch as _control


def main():
    parser = argparse.ArgumentParser(description="Run the all-user hourly transaction audit, GPT review and deterministic profit control loop.")
    parser.add_argument("--hours", type=float, default=1.0, help="Lookback window before the built-in 15-minute overlap (default: 1 hour)")
    parser.add_argument("--send-telegram", action="store_true", help="Send audit ZIP, GPT review and control-loop status to active MASTER Telegram account(s)")
    parser.add_argument("--skip-gpt", action="store_true", help="Collect the audit and run the deterministic control loop without calling the OpenAI API")
    args = parser.parse_args()

    app = AppSettings.load()
    result = run_transaction_audit(app, hours=max(0.25, args.hours))
    if args.skip_gpt:
        gpt_result = {
            "ok": False,
            "error": "GPT skipped by operator",
            "control_loop": _control.run_profit_control_loop(app, None),
        }
    else:
        # profit_control_loop_patch wraps this worker function, so manual and
        # scheduled hourly runs execute the identical GPT + deterministic loop.
        gpt_result = _worker.run_hourly_gpt_review(app, result["latest_zip"])

    if args.send_telegram:
        for tid in _worker._master_chat_ids(app):
            _worker.send_audit_document(app, tid, result["latest_zip"], result)
            _worker.send_gpt_review_message(app, tid, gpt_result)

    payload = {"audit": result, "gpt_review": gpt_result}
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if (args.skip_gpt or gpt_result.get("ok")) else 3


if __name__ == "__main__":
    raise SystemExit(main())
