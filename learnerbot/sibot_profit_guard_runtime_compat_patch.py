from __future__ import annotations

import threading

from . import sibot_profit_guard_patch as _guard

_ORIGINAL_MIGRATE = _guard._migrate_platform_once
_ORIGINAL_ENSURE = _guard.ensure_settings
_SETTINGS_LOCK = threading.RLock()


def _safe_migrate(app, path):
    # Some report/test helpers intentionally construct a lightweight app object
    # with csv_dir only. One-shot runtime migrations require the real data_dir
    # marker location, so skip migration for those incomplete helper objects.
    # The production App always has data_dir and still receives the migration.
    if not getattr(app, "data_dir", None):
        return None
    return _ORIGINAL_MIGRATE(app, path)


def _locked_ensure(app):
    # Several startup/report workers can request SiBot settings concurrently.
    # The underlying atomic CSV helper intentionally uses a fixed .tmp path, so
    # serialize initialization/migration to prevent two writers from replacing
    # the same temporary file at once.
    with _SETTINGS_LOCK:
        return _ORIGINAL_ENSURE(app)


_guard._migrate_platform_once = _safe_migrate
_guard.ensure_settings = _locked_ensure
_guard._sibot.ensure_settings = _locked_ensure
