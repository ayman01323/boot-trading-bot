from __future__ import annotations

from . import sibot_profit_guard_patch as _guard

_ORIGINAL_MIGRATE = _guard._migrate_platform_once


def _safe_migrate(app, path):
    # Some report/test helpers intentionally construct a lightweight app object
    # with csv_dir only. One-shot runtime migrations require the real data_dir
    # marker location, so skip migration for those incomplete helper objects.
    # The production App always has data_dir and still receives the migration.
    if not getattr(app, "data_dir", None):
        return None
    return _ORIGINAL_MIGRATE(app, path)


_guard._migrate_platform_once = _safe_migrate
