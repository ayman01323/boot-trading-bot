from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from . import ai_council_http_patch as _base

_SYNCED_RUNTIME_ENV = Path('/var/tmp/ai_council_runtime.env')
_ORIGINAL_RUNTIME_ENV = _base._runtime_env
_AUTHORITATIVE_SYNC_KEYS = {'COPILOT_GITHUB_TOKEN'}


def runtime_env_with_synced_fallback() -> dict[str, str]:
    """Use the writable sync bridge without disturbing healthy provider secrets.

    Most synced provider credentials remain fallback-only: an explicit process,
    repository or compatibility-bridge value keeps precedence.  Copilot is the
    bounded exception because the legacy compatibility file can be root-owned
    and stale while the credential sync has already live-tested a newer token.
    The sync workflow writes only the authenticated Copilot candidate as
    COPILOT_GITHUB_TOKEN, so that single key may replace a stale base value.

    No process-global environment is mutated and no secret is logged.
    """
    env = _ORIGINAL_RUNTIME_ENV()
    try:
        values = dotenv_values(_SYNCED_RUNTIME_ENV) or {}
        for key in getattr(_base, '_SECRET_KEYS', set()):
            value = values.get(key)
            if not value:
                continue
            name = str(key)
            if name in _AUTHORITATIVE_SYNC_KEYS or not str(env.get(name) or '').strip():
                env[name] = str(value)
    except Exception:
        pass
    return env


def install() -> None:
    _base._runtime_env = runtime_env_with_synced_fallback


install()
