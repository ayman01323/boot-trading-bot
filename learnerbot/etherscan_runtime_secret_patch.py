from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

from dotenv import dotenv_values

from . import config as _config

_PREV_LOAD = _config.AppSettings.load
_DEFAULT_RUNTIME_ENV = Path("/var/tmp/etherscan_runtime.env")


def _runtime_key() -> str:
    """Read only the Etherscan key from a protected external runtime file.

    Process/.env configuration remains authoritative. The bridge is a fallback
    for a GitHub Secret synced by the self-hosted runner and is intentionally
    outside the repository tree.
    """
    path = Path(os.environ.get("ETHERSCAN_RUNTIME_ENV") or _DEFAULT_RUNTIME_ENV)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            return ""
        values = dotenv_values(path) or {}
        return str(values.get("ETHERSCAN_API_KEY") or "").strip()
    except Exception:
        return ""


def _load_with_etherscan_runtime(cls):
    app = _PREV_LOAD()
    if str(app.etherscan_api_key or "").strip():
        return app
    key = _runtime_key()
    return replace(app, etherscan_api_key=key) if key else app


_config.AppSettings.load = classmethod(_load_with_etherscan_runtime)
