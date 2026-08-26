#!/usr/bin/env python3
"""Actual exec target for run.py's `start` command.

Fixes a real bug GPT's review caught: os.execvpe() replaces the process
image entirely, so any monkey-patching done in run.py's process before exec
(identity_patch.install(), the risk guard) is gone in the child -- a fresh
interpreter running `python -m learnerbot run` never sees it. The one
Telegram message run.py sends before exec gets the Claude prefix; nothing
the actual trading loop sends would have.

This script IS the exec target instead of `python -m learnerbot run`
directly: it installs the required patches first, in this process, then runs
learnerbot exactly the way `-m` would (runpy.run_module with
run_name="__main__" is what `-m` does internally) -- so the patch chain in
learnerbot/__main__.py still runs unmodified and in full, just with these two
patches already in place before it starts.
"""

from __future__ import annotations

import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent

for path in (THIS_DIR, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import claude_bot_patches

claude_bot_patches.install_all()

sys.argv = ["learnerbot", "run"]
import runpy

runpy.run_module("learnerbot", run_name="__main__")
