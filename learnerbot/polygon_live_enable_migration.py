from __future__ import annotations

import csv
import os
import time
from pathlib import Path

from . import cli as _cli
from .config import load_chains, load_dex_registry, load_kv_scoped
from .polygon_focus_patch import focus_enabled, set_focus
from .user_registry import set_user_setting, user_setting

POLYGON_CHAIN_ID = 137
TARGET_TELEGRAM_ID = "6760898817"
MARKER = ".polygon_live_enabled_20260821_v1"
VERIFIED_MARKER = ".polygon_live_verified_20260821_v1"
_PREV_APP = _cli._app


def _bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _rows(path: Path) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], ["chain_id", "setting", "value", "description"]
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or ["chain_id", "setting", "value", "description"])
        return list(reader), fields


def _upsert_scoped(path: Path, chain_id: str, values: dict[str, tuple[str, str]]) -> None:
    rows, fields = _rows(path)
    for name in ("chain_id", "setting", "value", "description"):
        if name not in fields:
            fields.append(name)
    by_key = {(str(r.get("chain_id") or "*"), str(r.get("setting") or "")): r for r in rows}
    for setting, (value, description) in values.items():
        key = (str(chain_id), str(setting))
        row = by_key.get(key)
        if row is None:
            row = {field: "" for field in fields}
            row["chain_id"] = str(chain_id)
            row["setting"] = str(setting)
            rows.append(row)
            by_key[key] = row
        row["value"] = str(value)
        row["description"] = str(description)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fields} for row in rows])
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _polygon_ready(app) -> tuple[bool, str]:
    chain = next((c for c in load_chains(app, enabled_only=False) if int(c.chain_id) == POLYGON_CHAIN_ID), None)
    if chain is None:
        return False, "Polygon chain 137 is not configured"
    if not bool(getattr(chain, "enabled", False)):
        return False, "Polygon chain 137 is disabled"

    venues = load_dex_registry(app.csv_dir, POLYGON_CHAIN_ID)
    executable = [
        row for row in venues
        if str(row.get("version") or "").strip().upper() == "V2"
        and _bool(row.get("enabled"))
        and _bool(row.get("auto_execute"))
    ]
    if not executable:
        return False, "Polygon has no enabled AUTO V2 venue"
    return True, ",".join(str(row.get("dex_name") or "V2") for row in executable)


def _set_user_polygon_live(app) -> None:
    values = {
        "auto_trading_enabled": ("true", "Polygon automatic execution explicitly enabled by owner"),
        "live_trading_enabled": ("true", "Polygon real-money signing explicitly enabled by owner"),
        "recommendation_mode": ("ARMED", "Polygon automatic execution explicitly armed"),
        "sibot_enabled": ("true", "EVM SiBot monitoring enabled on Polygon"),
        "sibot_auto_trade_enabled": ("true", "EVM SiBot LIVE copy execution enabled on Polygon"),
    }
    for setting, (value, description) in values.items():
        set_user_setting(
            app.csv_dir,
            TARGET_TELEGRAM_ID,
            setting,
            value,
            chain_id=str(POLYGON_CHAIN_ID),
            description=description,
        )


def verify_polygon_live(app) -> str:
    """Prove the owner-requested Polygon LIVE state was actually made effective once.

    This is intentionally a deployment-time invariant, not a permanent policy
    lock.  After a successful verification marker is written, the operator may
    later disable LIVE through normal controls without making future restarts fail.
    """
    ready, venues = _polygon_ready(app)
    if not ready:
        raise RuntimeError(venues)

    auto = load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", POLYGON_CHAIN_ID)
    live = load_kv_scoped(Path(app.csv_dir) / "live_trading_settings.csv", POLYGON_CHAIN_ID)
    checks = {
        "platform_auto": _bool(auto.get("auto_trading_enabled")),
        "fast_market": _bool(auto.get("fast_market_enabled")),
        "full_power": _bool(auto.get("full_power_enabled")),
        "polygon_live_gate": _bool(live.get("trading_enabled")),
        "polygon_focus": bool(focus_enabled(app)),
        "user_auto": _bool(user_setting(app.csv_dir, TARGET_TELEGRAM_ID, POLYGON_CHAIN_ID, "auto_trading_enabled", "false")),
        "user_live": _bool(user_setting(app.csv_dir, TARGET_TELEGRAM_ID, POLYGON_CHAIN_ID, "live_trading_enabled", "false")),
        "user_armed": str(user_setting(app.csv_dir, TARGET_TELEGRAM_ID, POLYGON_CHAIN_ID, "recommendation_mode", "SHADOW")).upper() == "ARMED",
        "user_sibot": _bool(user_setting(app.csv_dir, TARGET_TELEGRAM_ID, POLYGON_CHAIN_ID, "sibot_enabled", "false")),
        "user_sibot_auto": _bool(user_setting(app.csv_dir, TARGET_TELEGRAM_ID, POLYGON_CHAIN_ID, "sibot_auto_trade_enabled", "false")),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Polygon LIVE effective-state verification failed: " + ", ".join(failed))
    return venues


def enable_polygon_live(app) -> None:
    marker = Path(app.data_dir) / MARKER
    verified_marker = Path(app.data_dir) / VERIFIED_MARKER
    if verified_marker.exists():
        return

    ready, venues = _polygon_ready(app)
    if not ready:
        raise RuntimeError(venues)

    if not marker.exists():
        # Direct AUTO currently has a platform-wide emergency gate. Turn that gate
        # on only together with the audited Polygon-focus wrapper, which restricts
        # direct AUTO candidate execution to chain 137. User LIVE/ARMED, wallet,
        # route approval, exact quote, gas, simulation and minimum-net gates still
        # run at execution time.
        _upsert_scoped(
            Path(app.csv_dir) / "auto_trading_settings.csv",
            "*",
            {
                "auto_trading_enabled": ("true", "MASTER direct AUTO gate enabled for Polygon-focused execution"),
                "fast_market_enabled": ("true", "Keep hot executable market scanning enabled"),
                "full_power_enabled": ("true", "Keep full-power executable scanning enabled"),
            },
        )
        _upsert_scoped(
            Path(app.csv_dir) / "live_trading_settings.csv",
            str(POLYGON_CHAIN_ID),
            {
                "trading_enabled": ("true", "MASTER Polygon LIVE execution gate enabled"),
            },
        )
        _set_user_polygon_live(app)
        set_focus(app, True)

        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "\n".join(
                [
                    f"applied_epoch={int(time.time())}",
                    f"telegram_id={TARGET_TELEGRAM_ID}",
                    "chain_id=137",
                    "direct_auto_focus=polygon_only",
                    "platform_auto=true",
                    "polygon_live=true",
                    "user_mode=ARMED",
                    f"executable_v2_venues={venues}",
                    "route_scope=single_router_profit_protected_cycles",
                    "cross_dex_live=false",
                    "cross_dex_reason=requires_separate_atomic_cross_router_executor",
                    "profit_floor_and_simulation_unchanged=true",
                    "other_execution_safety_gates_unchanged=true",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    # The first rollout had a best-effort logging wrapper. Prove that its writes
    # really took effect before considering the owner request complete. If the
    # old rollout marker exists but an effective gate is missing, startup fails
    # here instead of silently claiming Polygon LIVE.
    venues = verify_polygon_live(app)
    verified_marker.parent.mkdir(parents=True, exist_ok=True)
    verified_marker.write_text(
        f"verified_epoch={int(time.time())}\nchain_id=137\nvenues={venues}\ncross_dex_live=false\n",
        encoding="utf-8",
    )
    print(
        "[polygon-live] verified=true enabled=true focus=polygon-only chain=137 "
        f"venues={venues} route_scope=single-router-profit-protected cross_dex=shadow-only"
    )


def _app_with_polygon_live():
    app = _PREV_APP()
    # Do not swallow this first-rollout verification. The bot must not advertise a
    # successful Polygon LIVE deployment if the effective runtime gates are absent.
    enable_polygon_live(app)
    return app


def install() -> None:
    if getattr(_cli, "_polygon_live_enable_migration_installed", False):
        return
    _cli._app = _app_with_polygon_live
    _cli._polygon_live_enable_migration_installed = True


install()
