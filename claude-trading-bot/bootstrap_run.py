#!/usr/bin/env python3
"""Actual exec target for run.py's `start` command.

Fixes a real bug GPT's review caught: os.execvpe() replaces the process
image entirely, so any monkey-patching done in run.py's process before exec
(identity_patch.install(), the risk guard, quarantine) is gone in the child
-- a fresh interpreter running `python -m learnerbot run` never sees it, and
sys.modules starts empty again too. Everything below has to happen fresh in
THIS process, in THIS exact order, every time.

This script IS the exec target instead of `python -m learnerbot run`
directly. Order is load-bearing and enforced by doing it directly at module
level in the sequence below, not left to caller discipline:
  1. claude_bot_quarantine.quarantine_before_any_learnerbot_import() --
     MUST be the first thing that touches learnerbot in this process. It
     stubs out historical production migrations via sys.modules
     pre-population (so their code never executes at all -- zero writes,
     not "writes limited to marker files", per review) and blanks
     production-only secret env vars, before learnerbot.config's
     un-overridden load_dotenv(BOT_ROOT/.env) or any migration's module-level
     code gets a chance to run.
  2. claude_bot_patches.install_all() -- identity prefix, Solana execution
     guard, EVM deny guard. These DO import learnerbot submodules
     (telegram, solana_live_executor, live_executor), which is why they
     must come after step 1, never before.
  3. runpy.run_module("learnerbot", run_name="__main__") -- functionally
     identical to `python -m learnerbot run`, so the patch chain in
     learnerbot/__main__.py still runs unmodified and in full, just with
     steps 1-2 already in place before it starts.
"""

from __future__ import annotations

import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent

for path in (THIS_DIR, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import claude_bot_quarantine

claude_bot_quarantine.quarantine_before_any_learnerbot_import()

import claude_bot_patches

claude_bot_patches.install_all()

sys.argv = ["learnerbot", "run"]
import runpy

runpy.run_module("learnerbot", run_name="__main__")
