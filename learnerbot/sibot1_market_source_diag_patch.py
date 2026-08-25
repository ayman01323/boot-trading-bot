from __future__ import annotations

import json
from pathlib import Path

from . import sibot1_runtime_diag_export_patch as _diag

_PREV_SNAPSHOT = _diag.snapshot


def _safe_int(value):
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _market_source_health(app) -> dict:
    path = Path(app.data_dir) / "sibot1" / "market_source_health.json"
    if not path.exists():
        return {
            "available": False,
            "redacted": True,
            "reason": "market_source_health_not_written_yet",
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "available": False,
            "redacted": True,
            "error": type(exc).__name__,
        }
    if not isinstance(raw, dict):
        return {"available": False, "redacted": True, "error": "invalid_payload"}

    evm = []
    for row in raw.get("evm") or []:
        if not isinstance(row, dict):
            continue
        evm.append({
            "file": Path(str(row.get("file") or "")).name,
            "exists": bool(row.get("exists")),
            "rows": _safe_int(row.get("rows")),
            "newest_age_seconds": _safe_int(row.get("newest_age_seconds")),
            "fresh_valid_rows": _safe_int(row.get("fresh_valid_rows")),
        })

    sol_raw = raw.get("solana") if isinstance(raw.get("solana"), dict) else {}
    solana = {
        "db_exists": bool(sol_raw.get("db_exists")),
        "leader_buys_15m": _safe_int(sol_raw.get("leader_buys_15m")),
        "leader_buys_6h": _safe_int(sol_raw.get("leader_buys_6h")),
        "profitable_trade_seeds_24h": _safe_int(sol_raw.get("profitable_trade_seeds_24h")),
        "newest_leader_buy_age_seconds": _safe_int(sol_raw.get("newest_leader_buy_age_seconds")),
    }
    if sol_raw.get("error"):
        solana["error"] = str(sol_raw.get("error"))[:80]

    by_chain_raw = raw.get("events_by_chain") if isinstance(raw.get("events_by_chain"), dict) else {}
    windows_raw = raw.get("windows_seconds") if isinstance(raw.get("windows_seconds"), dict) else {}
    return {
        "available": True,
        "redacted": True,
        "updated_epoch": _safe_int(raw.get("updated_epoch")),
        "relaxed_discovery_only": bool(raw.get("relaxed_discovery_only")),
        "execution_safety_unchanged": bool(raw.get("execution_safety_unchanged")),
        "events_emitted_last_poll": _safe_int(raw.get("events_emitted")),
        "events_by_chain_last_poll": {
            "base": _safe_int(by_chain_raw.get("base")) or 0,
            "solana": _safe_int(by_chain_raw.get("solana")) or 0,
        },
        "evm": evm,
        "solana": solana,
        "windows_seconds": {
            "primary_leader": _safe_int(windows_raw.get("primary_leader")),
            "fallback_leader": _safe_int(windows_raw.get("fallback_leader")),
            "profitable_trade_seed": _safe_int(windows_raw.get("profitable_trade_seed")),
        },
    }


def snapshot(app) -> dict:
    out = _PREV_SNAPSHOT(app)
    out["schema_version"] = max(5, int(out.get("schema_version") or 0))
    out["market_source_health"] = _market_source_health(app)
    return out


def install() -> None:
    if getattr(_diag, "_sibot1_market_source_diag_installed", False):
        return
    _diag.snapshot = snapshot
    _diag._sibot1_market_source_diag_installed = True
    print("[sibot1-market-source-diag] redacted=true source-health=true")


install()
