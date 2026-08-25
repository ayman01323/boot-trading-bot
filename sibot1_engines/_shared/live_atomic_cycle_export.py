from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import live_candidate_export as _base_export
from . import runtime as _runtime

_INSTALLED = False
_PREV_TRADE = _runtime.SiBot1ShadowRuntime._handle_trade


def _b(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "pass", "ok"}


def _score_counts(runtime, engine_id: str, chain: str) -> tuple[int, int]:
    entries = exits = 0
    for row in runtime.scoreboard.snapshot():
        if str(row.get("engine_id") or "") == engine_id and str(row.get("chain") or "").lower() == chain.lower():
            entries = int(row.get("paper_entries") or 0)
            exits = int(row.get("paper_exits") or 0)
            break
    return entries, exits


def _source_row(intent) -> dict[str, str] | None:
    meta = dict(intent.metadata or {})
    source = str(meta.get("source_path") or "").strip()
    route = ">".join(str(x).strip() for x in (meta.get("route_path") or ()) if str(x).strip())
    if not source or not route:
        return None
    path = Path(source)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))[-300:]
    except Exception:
        return None
    wanted_chain = str(intent.chain or "").strip().lower()
    wanted_epoch = int(intent.created_at_ms or 0) // 1000
    for row in reversed(rows):
        row_route = str(row.get("route_path") or "").strip()
        row_chain = str(row.get("chain_slug") or "").strip().lower()
        try:
            row_epoch = int(float(row.get("observed_at_epoch") or 0))
        except Exception:
            row_epoch = 0
        if row_route == route and row_chain == wanted_chain and abs(row_epoch - wanted_epoch) <= 1:
            return dict(row)
    return None


def _trade(runtime, intent):
    meta = dict(getattr(intent, "metadata", {}) or {})
    is_atomic = str(getattr(intent, "side", "")).upper() == "ARBITRAGE" and str(meta.get("execution_family") or "").upper() == "ATOMIC_CYCLE"
    if not is_atomic:
        return _PREV_TRADE(runtime, intent)

    try:
        decision = runtime.poolcheck.assess_entry(intent)
        verdict = str(decision.verdict or "HARD_BLOCK").upper()
        reasons = list(decision.reasons or ())[:8]
    except Exception as exc:
        verdict = "HARD_BLOCK"
        reasons = [f"POOLCHECK_ERROR:{type(exc).__name__}"]

    before_entries, before_exits = _score_counts(runtime, str(intent.engine_id), str(intent.chain))
    result = _PREV_TRADE(runtime, intent)
    after_entries, after_exits = _score_counts(runtime, str(intent.engine_id), str(intent.chain))

    # LIVE export is stricter than paper: central PoolCheck must be PASS and the
    # paper atomic estimate must have completed both entry + exit accounting.
    if verdict != "PASS" or after_entries <= before_entries or after_exits <= before_exits:
        return result

    row = _source_row(intent)
    if not row:
        runtime.scoreboard.error(intent.engine_id, intent.chain, "atomic-cycle live export could not recover source route row")
        return result

    route_kind = str(row.get("route_kind") or "").upper()
    execution_mode = str(row.get("execution_mode") or "").upper()
    if route_kind not in {"V2_CYCLE", "V3_CYCLE"} or route_kind.startswith("CROSS_") or execution_mode.startswith("SHADOW"):
        return result

    required = (
        "exact_quote_ok",
        "simulation_ok",
        "liquidity_ok",
        "route_approved",
        "whole_route_approved",
        "atomic_profit_protection",
    )
    if not all(_b(row.get(key)) for key in required):
        return result

    route_path = str(row.get("route_path") or "").strip()
    route = [x.strip() for x in route_path.split(">") if x.strip()]
    if len(route) < 3 or route[0].lower() != route[-1].lower():
        return result

    expected_net = Decimal(str(getattr(intent, "expected_net_profit", 0) or 0))
    if expected_net <= 0:
        return result

    _base_export._append(runtime, {
        "candidate_id": str(intent.intent_id),
        "kind": "ARBITRAGE",
        "engine_id": str(intent.engine_id),
        "engine_version": str(intent.engine_version),
        "strategy_id": str(intent.strategy_id),
        "chain": str(intent.chain).lower(),
        "asset_in": str(intent.asset_in),
        "asset_out": str(intent.asset_out),
        "intent_created_at_ms": int(intent.created_at_ms),
        "market_event_id": str(intent.market_event_id or ""),
        "poolcheck_verdict": verdict,
        "poolcheck_reasons": reasons,
        "route_id": str(row.get("route_id") or ""),
        "route_kind": route_kind,
        "route_path": route_path,
        "route_fees": str(row.get("route_fees") or ""),
        "router_address": str(row.get("router_address") or ""),
        "quoter_address": str(row.get("quoter_address") or ""),
        "execution_mode": execution_mode,
        "expected_net_profit_virtual": str(expected_net),
        "gross_edge_bps": str(meta.get("gross_edge_bps") or ""),
        "estimated_cost_bps": str(meta.get("estimated_cost_bps") or ""),
        "net_edge_bps": str(meta.get("net_edge_bps") or ""),
        "source_path": str(meta.get("source_path") or ""),
    })
    runtime.scoreboard.audit(
        "LIVE_CANDIDATE_EXPORT",
        engine_id=str(intent.engine_id),
        chain=str(intent.chain),
        intent_id=str(intent.intent_id),
        kind="ARBITRAGE",
        route_id=str(row.get("route_id") or ""),
        route_kind=route_kind,
    )
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _runtime.SiBot1ShadowRuntime._handle_trade = _trade
    _INSTALLED = True


install()
