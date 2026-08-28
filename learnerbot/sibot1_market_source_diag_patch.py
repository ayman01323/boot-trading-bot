from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from . import sibot1_runtime_diag_export_patch as _diag

_PREV_SNAPSHOT = _diag.snapshot


def _safe_int(value):
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _csv_rows(path: Path) -> list[dict]:
    try:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _safe_rejection_code(stage: str, reason: str) -> str:
    """Return a bounded non-secret diagnostic class, never raw provider text."""
    s = str(stage or "unknown").strip().lower() or "unknown"
    r = str(reason or "").strip().lower()
    known = (
        ("no_complete_v2_triangle_in_persisted_registry", "no_complete_v2_triangle"),
        ("no_complete_v3_triangle", "no_complete_v3_triangle"),
        ("missing_quoter", "missing_quoter"),
        ("factory_has_no_code", "factory_no_code"),
        ("router_has_no_code", "router_no_code"),
        ("quoter_has_no_code", "quoter_no_code"),
        ("price_impact", "price_impact"),
        ("product", "product_policy"),
        ("quarantine", "quarantine"),
        ("edge", "edge_floor"),
        ("profit", "profit_floor"),
        ("timeout", "provider_timeout"),
        ("429", "provider_rate_limit"),
        ("rate limit", "provider_rate_limit"),
        ("connection", "provider_connection"),
        ("rpc", "provider_rpc"),
    )
    for needle, code in known:
        if needle in r:
            return code
    return f"stage_{s[:40]}"


def _fast_market_health(app) -> dict:
    auto_dir = Path(app.csv_dir) / "auto"
    rows = _csv_rows(auto_dir / "fast_market_status.csv")
    latest = rows[-1] if rows else {}
    result = {
        "available": bool(rows),
        "redacted": True,
        "updated_epoch": _safe_int(latest.get("updated_epoch")),
        "duration_seconds": str(latest.get("duration_seconds") or "")[:24],
        "routes": _safe_int(latest.get("routes")) or 0,
        "merged_routes": _safe_int(latest.get("merged_routes")) or 0,
        "eligible": _safe_int(latest.get("eligible")) or 0,
        "auto_events": _safe_int(latest.get("auto_events")) or 0,
        "status": str(latest.get("status") or "UNKNOWN")[:32],
    }
    note = str(latest.get("note") or "")
    if note:
        result["note_class"] = "output_file" if note.endswith(".csv") else "error_detail_redacted"
    return result


def _base_fast_market_health(app) -> dict:
    """Expose aggregate health for the dedicated Base hot feed without raw routes."""
    auto_dir = Path(app.csv_dir) / "auto"
    rows = _csv_rows(auto_dir / "base_fast_market_status.csv")
    latest = rows[-1] if rows else {}
    result = {
        "available": bool(rows),
        "redacted": True,
        "updated_epoch": _safe_int(latest.get("updated_epoch")),
        "duration_seconds": str(latest.get("duration_seconds") or "")[:24],
        "routes": _safe_int(latest.get("routes")) or 0,
        "provider_pressure": _safe_int(latest.get("provider_pressure")) or 0,
        "checks_budget": _safe_int(latest.get("checks_budget")) or 0,
        "routes_budget": _safe_int(latest.get("routes_budget")) or 0,
        "status": str(latest.get("status") or "UNKNOWN")[:32],
    }
    note = str(latest.get("note") or "")
    if note:
        result["note_class"] = "output_file" if note.endswith(".csv") else "error_detail_redacted"
    return result


def _registry_health(app) -> dict:
    auto_dir = Path(app.csv_dir) / "auto"
    out = {}
    for name in ("pool_registry.csv", "v3_pool_registry.csv"):
        rows = _csv_rows(auto_dir / name)
        by_chain = Counter(str(r.get("chain_id") or "unknown") for r in rows)
        out[name] = {
            "exists": (auto_dir / name).exists(),
            "rows": len(rows),
            "rows_by_chain": dict(sorted(by_chain.items())),
        }
    return out


def _rejection_health(app) -> dict:
    auto_dir = Path(app.csv_dir) / "auto"
    out = {}
    for name in (
        "full_power_rejections.csv",
        "base_full_power_rejections.csv",
        "power_discovery_rejections.csv",
    ):
        rows = _csv_rows(auto_dir / name)
        stage_counts = Counter()
        code_counts = Counter()
        chain_counts = Counter()
        for row in rows[-500:]:
            stage = str(row.get("stage") or "unknown").strip().lower() or "unknown"
            reason = str(row.get("reason") or "")
            stage_counts[stage[:40]] += 1
            code_counts[_safe_rejection_code(stage, reason)] += 1
            chain_counts[str(row.get("chain_id") or "unknown")] += 1
        out[name] = {
            "exists": (auto_dir / name).exists(),
            "rows_tail": min(500, len(rows)),
            "stage_counts": dict(stage_counts.most_common(12)),
            "reason_class_counts": dict(code_counts.most_common(12)),
            "rows_by_chain": dict(sorted(chain_counts.items())),
            "raw_reasons_exported": False,
        }
    return out


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


def _engine_nomination_health(app) -> dict:
    """Expose only aggregate counters from worker health; never candidate identities."""
    path = Path(app.data_dir) / "sibot1" / "status.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "redacted": True, "engines": {}}
    workers = raw.get("workers") if isinstance(raw, dict) else {}
    if not isinstance(workers, dict):
        return {"available": False, "redacted": True, "engines": {}}

    engines = {}
    for engine_id in ("gpt", "gemini", "grok"):
        row = workers.get(engine_id)
        if not isinstance(row, dict):
            continue
        item = {
            "events": _safe_int(row.get("events")) or 0,
            "signals": _safe_int(row.get("signals")) or 0,
        }
        rejects = row.get("prefilter_rejections")
        if isinstance(rejects, dict):
            item["prefilter_rejections"] = {
                str(k)[:80]: max(0, _safe_int(v) or 0)
                for k, v in rejects.items()
            }
        for key in (
            "developer_flow_known_safe",
            "developer_flow_selling",
            "developer_flow_unknown",
            "spread_signals",
            "cycle_signals",
        ):
            if key in row:
                item[key] = max(0, _safe_int(row.get(key)) or 0)
        engines[engine_id] = item
    return {"available": True, "redacted": True, "engines": engines}


def snapshot(app) -> dict:
    out = _PREV_SNAPSHOT(app)
    out["schema_version"] = max(8, int(out.get("schema_version") or 0))
    out["market_source_health"] = _market_source_health(app)
    out["engine_nomination_health"] = _engine_nomination_health(app)
    out["evm_fast_market_health"] = _fast_market_health(app)
    out["base_fast_market_health"] = _base_fast_market_health(app)
    out["evm_pool_registry_health"] = _registry_health(app)
    out["evm_rejection_health"] = _rejection_health(app)
    return out


def install() -> None:
    if getattr(_diag, "_sibot1_market_source_diag_installed", False):
        return
    _diag.snapshot = snapshot
    _diag._sibot1_market_source_diag_installed = True
    print("[sibot1-market-source-diag] redacted=true source-health=true nomination-health=true evm-funnel=true base-funnel=true")


install()
