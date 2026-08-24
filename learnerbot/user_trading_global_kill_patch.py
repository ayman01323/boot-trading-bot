from __future__ import annotations

"""Harden user-level trading switches without breaking chain disable overrides.

Historical CSVs may contain duplicate ``*`` rows, legacy ``0``/blank aliases, or
old real-chain rows. A user-level global OFF must always win, while an explicit
per-chain OFF may still narrow an otherwise-global ON.
"""

from . import user_registry as _ur

_GLOBAL_BOOLEAN_KILL_SETTINGS = frozenset({
    "auto_trading_enabled",
    "live_trading_enabled",
})
_GLOBAL_ONLY_SETTINGS = frozenset({"recommendation_mode"})
_CONTROL_SETTINGS = _GLOBAL_BOOLEAN_KILL_SETTINGS | _GLOBAL_ONLY_SETTINGS

_ORIGINAL_USER_SETTING = _ur.user_setting
_ORIGINAL_SET_USER_SETTING = _ur.set_user_setting


def _setting_name(row: dict) -> str:
    return str(row.get("setting") or "").strip()


def _tid(row: dict) -> str:
    return str(row.get("telegram_id") or "").strip()


def _scope(row: dict) -> str:
    return str(row.get("chain_id", "*") or "").strip()


def _global_value(rows, tid: str, setting: str, default=None):
    value = default
    found = False

    # Legacy blank/0 rows are fallback only.
    for row in rows:
        if _tid(row) != tid or _setting_name(row) != setting:
            continue
        if _scope(row) in {"", "0"}:
            value = row.get("value", default)
            found = True

    # Canonical '*' always wins over legacy aliases.
    for row in rows:
        if _tid(row) != tid or _setting_name(row) != setting:
            continue
        if _scope(row) == "*":
            value = row.get("value", default)
            found = True

    return value, found


def user_setting(csv_dir, telegram_id, chain_id, setting: str, default=None):
    setting = str(setting)
    if setting not in _CONTROL_SETTINGS:
        return _ORIGINAL_USER_SETTING(csv_dir, telegram_id, chain_id, setting, default)

    tid = str(telegram_id).strip()
    target_scope = str(chain_id).strip()
    rows = _ur._rows(_ur.user_settings_path(csv_dir))
    value, global_found = _global_value(rows, tid, setting, default)

    if setting in _GLOBAL_ONLY_SETTINGS:
        return value

    # For LIVE/AUTOTRADE, an explicit global OFF is a hard kill and can never
    # be resurrected by a stale real-chain true row.
    if global_found and not _ur._bool(value, False):
        return value

    # When global is ON (or absent), preserve the existing ability to narrow
    # one real chain with an explicit chain-specific override. This lets an
    # operator keep BSC OFF while Base remains ON, but never defeats global OFF.
    if target_scope not in {"", "*", "0"}:
        for row in rows:
            if _tid(row) != tid or _setting_name(row) != setting:
                continue
            if _scope(row) == target_scope:
                value = row.get("value", default)

    return value


def _dedupe_scoped_write(rows, tid: str, scope: str, setting: str, value, description: str):
    matches = []
    kept = []
    for row in rows:
        if _tid(row) == tid and _scope(row) == scope and _setting_name(row) == setting:
            matches.append(row)
        else:
            kept.append(row)

    row = dict(matches[-1]) if matches else {
        "telegram_id": tid,
        "chain_id": scope,
        "setting": setting,
        "value": str(value),
        "description": description,
    }
    row["telegram_id"] = tid
    row["chain_id"] = scope
    row["setting"] = setting
    row["value"] = str(value)
    if description:
        row["description"] = description
    kept.append(row)
    return kept


def set_user_setting(csv_dir, telegram_id, setting: str, value, *, chain_id="*", description=""):
    """Make canonical global writes idempotent and remove stale control rows.

    Telegram ``/autotrade`` and ``/live`` write the canonical ``*`` scope. Such
    a write replaces all obsolete aliases and chain rows for the same control,
    leaving exactly one authoritative global row. Explicit legacy or chain
    writes remain compatible and are deduplicated only within their own scope.
    """
    path = _ur.user_settings_path(csv_dir)
    rows = _ur._rows(path)
    tid = str(telegram_id).strip()
    setting = str(setting).strip()
    scope = str(chain_id).strip()

    if setting in _CONTROL_SETTINGS and scope == "*":
        kept = [r for r in rows if not (_tid(r) == tid and _setting_name(r) == setting)]
        kept.append({
            "telegram_id": tid,
            "chain_id": "*",
            "setting": setting,
            "value": str(value),
            "description": description,
        })
        _ur._atomic_write(path, kept, _ur.USER_SETTING_HEADERS)
        return

    kept = _dedupe_scoped_write(rows, tid, scope, setting, value, description)
    _ur._atomic_write(path, kept, _ur.USER_SETTING_HEADERS)


def install():
    if getattr(_ur, "_global_user_trading_kill_patch_installed", False):
        return
    _ur.user_setting = user_setting
    _ur.set_user_setting = set_user_setting
    _ur._global_user_trading_kill_patch_installed = True


install()
