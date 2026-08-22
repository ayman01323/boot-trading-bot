from __future__ import annotations

import threading
from pathlib import Path

from . import sibot_profit_guard_patch as _guard
from . import sibot_reasonable_top20_patch as _reasonable

_ORIGINAL_MIGRATE = _guard._migrate_platform_once
_ORIGINAL_ENSURE = _guard.ensure_settings
_SETTINGS_LOCK = threading.RLock()


def _safe_migrate(app, path):
    # Some report/test helpers intentionally construct a lightweight app object
    # with csv_dir only. One-shot runtime migrations require the real data_dir
    # marker location, so skip migration for those incomplete helper objects.
    # The production App always has data_dir and still receives the migration.
    data_dir = getattr(app, "data_dir", None)
    if not data_dir:
        return None
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    return _ORIGINAL_MIGRATE(app, path)


def _locked_ensure(app):
    # Several startup/report workers can request SiBot settings concurrently.
    # The underlying atomic CSV helper intentionally uses a fixed .tmp path, so
    # serialize initialization/migration to prevent two writers from replacing
    # the same temporary file at once.
    with _SETTINGS_LOCK:
        path = _ORIGINAL_ENSURE(app)
        # The old quality-guard v1 migration can still write the historical
        # require_complete_history=true value on a fresh data directory before it
        # creates its marker.  Re-apply the current compatibility migration after
        # that one-shot migration, under the same lock, so the final persisted value
        # from this very settings read is the current policy value (false).
        # sibot_quality_compat_patch replaces this hook with a single-key,
        # idempotent correction and keeps all old 50->5 / 55->50 relaxations blocked.
        _reasonable._migrate_reasonable_defaults(app, path)
        return path


_guard._migrate_platform_once = _safe_migrate
_guard.ensure_settings = _locked_ensure
_guard._sibot.ensure_settings = _locked_ensure
