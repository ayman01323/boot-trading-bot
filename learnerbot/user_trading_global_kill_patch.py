from __future__ import annotations

"""Harden user-level trading switches as true global controls.

Historical CSVs may contain duplicate ``*`` rows, legacy ``0``/blank aliases, or
old real-chain rows for settings that are now user-level master switches.  Those
rows must never be able to keep signing/AUTO enabled after the user sends an OFF
command.

Only the three control-plane settings below are global-only.  Position sizing,
profit thresholds, gas settings and other numerical policy remain chain-
overridable through the normal user_registry resolver.
"""

from . import user_registry as _ur

_GLOBAL_ONLY_SETTINGS = frozenset({
    "auto_trading_enabled",
    "live_trading_enabled",
    "recommendation_mode",
})

_ORIGINAL_USER_SETTING = _ur.user_setting
_ORIGINAL_SET_USER_SETTING = _ur.set_user_setting


def _setting_name(row: dict) -> str:
    return str(row.get("setting") or "").strip()


def _tid(row: dict) -> str:
    return str(row.get("telegram_id") or "").strip()


def _scope(row: dict) -> str:
    return str(row.get("chain_id", "*") or "").strip()


def user_setting(csv_dir, telegram_id, chain_id, setting: str, default=None):
    """Resolve global control switches without any real-chain override.

    For global-only controls, legacy blank/0 rows are fallback values and the
    canonical ``*`` row wins.  A real chain row is intentionally ignored so a
    stale chain-specific ``true`` can never defeat global ``/autotrade off`` or
    ``/live off``.
    """
    if str(setting) not in _GLOBAL_ONLY_SETTINGS:
        return _ORIGINAL_USER_SETTING(csv_dir, telegram_id, chain_id, setting, default)

    tid = str(telegram_id).strip()
    value = default
    rows = _ur._rows(_ur.user_settings_path(csv_dir))

    for row in rows:
        if _tid(row) != tid or _setting_name(row) != setting:
            continue
        if _scope(row) in {"", "0"}:
            value = row.get("value", default)

    for row in rows:
        if _tid(row) != tid or _setting_name(row) != setting:
            continue
        if _scope(row) == "*":
            value = row.get("value", default)

    return value


def set_user_setting(csv_dir, telegram_id, setting: str, value, *, chain_id="*", description=""):
    """Write one canonical row for global controls and dedupe exact rows.

    Global-only controls are normalised to one ``*`` row and all obsolete
    aliases/chain rows for the same user+setting are removed.  This makes ON/OFF
    idempotent and ensures the user-level OFF command is a hard kill across every
    EVM chain.
    """
    path = _ur.user_settings_path(csv_dir)
    rows = _ur._rows(path)
    tid = str(telegram_id).strip()
    setting = str(setting).strip()

    if setting in _GLOBAL_ONLY_SETTINGS:
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

    scope = str(chain_id)
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
    _ur._atomic_write(path, kept, _ur.USER_SETTING_HEADERS)


def install():
    if getattr(_ur, "_global_user_trading_kill_patch_installed", False):
        return
    _ur.user_setting = user_setting
    _ur.set_user_setting = set_user_setting
    _ur._global_user_trading_kill_patch_installed = True


install()
