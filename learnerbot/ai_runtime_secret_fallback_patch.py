from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from . import ai_council_http_patch as _base

_SYNCED_RUNTIME_ENV = Path('/var/tmp/ai_council_runtime.env')
_ORIGINAL_RUNTIME_ENV = _base._runtime_env


def runtime_env_with_synced_fallback() -> dict[str, str]:
    """Overlay provider credentials from the freshly synced writable bridge.

    The GitHub Actions credential sync writes /var/tmp/ai_council_runtime.env
    atomically before attempting the legacy compatibility copy under
    /var/tmp/boot.  The compatibility file can be root-owned and therefore
    become stale.  For provider secret keys, a value in the freshly synced
    writable bridge is therefore newer and authoritative and must replace any
    stale process/repository/compatibility value.  Model/config values remain
    untouched.  No process-global environment is mutated and no secret is
    logged.
    """
    env = _ORIGINAL_RUNTIME_ENV()
    try:
        values = dotenv_values(_SYNCED_RUNTIME_ENV) or {}
        for key in getattr(_base, '_SECRET_KEYS', set()):
            value = values.get(key)
            if value:
                env[str(key)] = str(value)
    except Exception:
        pass
    return env


def install() -> None:
    _base._runtime_env = runtime_env_with_synced_fallback


install()
