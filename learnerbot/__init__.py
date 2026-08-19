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
