from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from . import ai_council_http_patch as _base

_SYNCED_RUNTIME_ENV = Path('/var/tmp/ai_council_runtime.env')
_ORIGINAL_RUNTIME_ENV = _base._runtime_env


def runtime_env_with_synced_fallback() -> dict[str, str]:
    """Fill missing provider credentials from the writable synced bridge.

    Explicit process, repository or compatibility-bridge credentials retain
    precedence. The writable /var/tmp bridge is only a fallback for missing
    provider secrets. No process-global environment is mutated and no secret is
    logged.
    """
    env = _ORIGINAL_RUNTIME_ENV()
    try:
        values = dotenv_values(_SYNCED_RUNTIME_ENV) or {}
        for key in getattr(_base, '_SECRET_KEYS', set()):
            value = values.get(key)
            if value and not str(env.get(str(key)) or '').strip():
                env[str(key)] = str(value)
    except Exception:
        pass
    return env


def install() -> None:
    _base._runtime_env = runtime_env_with_synced_fallback


install()
