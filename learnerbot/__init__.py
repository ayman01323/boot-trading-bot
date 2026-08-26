from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

__version__ = "2.3.10"

_STARTUP_ERROR_PATH = Path("/tmp/learnerbot-startup-error.txt")
_PREVIOUS_EXCEPTHOOK = sys.excepthook


def _startup_excepthook(exc_type, exc_value, exc_traceback):
    """Persist an uncaught startup/runtime exception without dumping environment data."""
    try:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        _STARTUP_ERROR_PATH.write_text(text[-20000:], encoding="utf-8")
        os.chmod(_STARTUP_ERROR_PATH, 0o644)
    except Exception:
        pass
    _PREVIOUS_EXCEPTHOOK(exc_type, exc_value, exc_traceback)


try:
    _STARTUP_ERROR_PATH.unlink(missing_ok=True)
except Exception:
    pass
sys.excepthook = _startup_excepthook

# Safety-critical compatibility layer: user LIVE/AUTOTRADE/mode are global
# master controls and must not be defeated by stale per-chain CSV rows.
from . import user_trading_global_kill_patch as _user_trading_global_kill_patch  # noqa: E402,F401

# The full-power graph scanner used a deterministic tiny prefix of already-known
# routes on every pass. Rotate discovery across the larger verified pool graph and
# raise only the read-only candidate sampling budget; execution safety is unchanged.
from . import full_power_candidate_rotation_patch as _full_power_candidate_rotation_patch  # noqa: E402,F401

# Supervise the independent SiBot 1 SHADOW/PAPER sidecar only for `learnerbot run`.
# Its child launch is delayed so MAIN BOOT can finish its audited fail-closed
# composition checks first. This import never enables signing or broadcast.
from . import sibot1_shadow_runtime_patch as _sibot1_shadow_runtime_patch  # noqa: E402,F401
