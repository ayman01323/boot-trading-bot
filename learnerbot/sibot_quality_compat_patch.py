from __future__ import annotations

from . import sibot_reasonable_top20_patch as _reasonable


def _no_legacy_relaxation(app, path):
    # The old patch still owns broad profit-first Top-20 research, but its one-time
    # relaxed leader-default migration is superseded by the new quality guard.
    return None


_reasonable._migrate_reasonable_defaults = _no_legacy_relaxation
