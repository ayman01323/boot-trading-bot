from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .config import AppSettings

_RUNTIME_FILE = Path("/var/tmp/boot_evm_history_runtime.env")
_PREV_LOAD = AppSettings.load.__func__


def _runtime_etherscan_key() -> str:
    """Read only the Etherscan credential from the root-readable runtime bridge.

    The bridge contains no trading wallet material and its value is never logged or
    persisted into repository/data diagnostics. A normal bot .env value remains
    authoritative and wins over this fallback.
    """
    try:
        for raw in _RUNTIME_FILE.read_text(encoding="utf-8").splitlines():
            if not raw.startswith("ETHERSCAN_API_KEY="):
                continue
            value = raw.split("=", 1)[1].strip()
            if not value:
                return ""
            try:
                return str(json.loads(value)).strip()
            except Exception:
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


@classmethod
def load_with_evm_history_runtime_secret(cls):
    app = _PREV_LOAD(cls)
    if str(app.etherscan_api_key or "").strip():
        return app
    key = _runtime_etherscan_key()
    if not key:
        return app
    return replace(app, etherscan_api_key=key)


def install():
    if getattr(AppSettings, "_evm_history_runtime_secret_patch_installed", False):
        return
    AppSettings.load = load_with_evm_history_runtime_secret
    AppSettings._evm_history_runtime_secret_patch_installed = True


install()
