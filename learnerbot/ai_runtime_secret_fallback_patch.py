from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from . import ai_council_http_patch as _base

_SYNCED_RUNTIME_ENV = Path('/var/tmp/ai_council_runtime.env')
_ORIGINAL_RUNTIME_ENV = _base._runtime_env


def runtime_env_with_synced_fallback() -> dict[str, str]:
    """Make the writable synced runtime bridge authoritative for provider secrets.

    GitHub Actions writes ``/var/tmp/ai_council_runtime.env`` from repository
    secrets with mode 600.  A legacy compatibility file, process environment,
    or repository ``.env`` can be stale after credential rotation, so any
    provider secret present in the synced bridge must replace the older value.

    Model/config values still come from the normal service environment/.env.
    No process-global environment is mutated and no secret is logged.  If the
    synced bridge is temporarily unavailable, the existing environment remains
    usable as a fail-open fallback.
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
