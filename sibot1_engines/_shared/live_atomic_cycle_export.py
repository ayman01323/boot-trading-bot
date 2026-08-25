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


def _route_contract(row: dict[str, str], source_path: str) -> tuple[str, str] | None:
    """Resolve a same-executor route contract without inventing LIVE approval.

    Older/current wallet-neutral scanners intentionally leave route_kind and
    execution_mode blank even though they are exact V2 cycle scanners. Infer that
    metadata only for the two canonical cycle feeds and only when the row itself
    proves a closed, exact, single-router route. Explicit SHADOW/CROSS metadata is
    never overridden.
    """
    route_kind = str(row.get("route_kind") or "").strip().upper()
    execution_mode = str(row.get("execution_mode") or "").strip().upper()
    if route_kind.startswith("CROSS_") or execution_mode.startswith("SHADOW"):
        return None

    route = [x.strip() for x in str(row.get("route_path") or "").split(">") if x.strip()]
    source_name = Path(source_path).name
    inferable_v2 = (
        source_name in {"direct_market_opportunities.csv", "learned_route_opportunities.csv", "live_opportunities.csv"}
        and len(route) >= 3
        and route[0].lower() == route[-1].lower()
        and bool(str(row.get("router_address") or "").strip())
        and _b(row.get("scanner_exact"))
        and _b(row.get("source_verified"))
    )
    if not route_kind and inferable_v2:
        route_kind = "V2_CYCLE"
    if not execution_mode and inferable_v2:
        execution_mode = "LIVE_REVALIDATE_REQUIRED"
    if route_kind not in {"V2_CYCLE", "V3_CYCLE"}:
        return None
    return route_kind, execution_mode or "LIVE_REVALIDATE_REQUIRED"


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

    # The shared runtime is SHADOW/PAPER. PASS or SHADOW_ONLY may nominate a
    # current cycle, but neither is inherited as LIVE approval. HARD_BLOCK and
    # COOLING never leave SHADOW.
    if verdict not in {"PASS", "SHADOW_ONLY"} or after_entries <= before_entries or after_exits <= before_exits:
        return result

    row = _source_row(intent)
    if not row:
        runtime.scoreboard.error(intent.engine_id, intent.chain, "atomic-cycle live nomination could not recover source route row")
        return result

    source_path = str(meta.get("source_path") or "")
    contract = _route_contract(row, source_path)
    if not contract:
        return result
    route_kind, execution_mode = contract

    # Nomination requires current scan-time facts only. Wallet-specific simulation,
    # sellability/profit protection and all route-rug checks are intentionally
    # performed again by the separate LIVE bridge immediately before signing.
    required_scan = (
        "exact_quote_ok",
        "liquidity_ok",
        "route_approved",
        "whole_route_approved",
    )
    if not all(_b(row.get(key)) for key in required_scan):
        return result

    route_path = str(row.get("route_path") or "").strip()
    route = [x.strip() for x in route_path.split(">") if x.strip()]
    if len(route) < 3 or route[0].lower() != route[-1].lower():
        return result

    expected_net = Decimal(str(getattr(intent, "expected_net_profit", 0) or 0))
    if expected_net <= 0:
        return result

    source_simulation_ok = _b(row.get("simulation_ok"))
    source_sellability_ok = _b(row.get("sellability_ok"))
    source_atomic_profit = _b(row.get("atomic_profit_protection"))

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
        "shadow_poolcheck_verdict": verdict,
        "shadow_poolcheck_reasons": reasons,
        "live_revalidation_required": True,
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
        "source_path": source_path,
        "source_simulation_ok": source_simulation_ok,
        "source_sellability_ok": source_sellability_ok,
        "source_atomic_profit_protection": source_atomic_profit,
        "source_preflight_complete": bool(source_simulation_ok and source_sellability_ok and source_atomic_profit),
    })
    runtime.scoreboard.audit(
        "LIVE_CANDIDATE_NOMINATION",
        engine_id=str(intent.engine_id),
        chain=str(intent.chain),
        intent_id=str(intent.intent_id),
        kind="ARBITRAGE",
        route_id=str(row.get("route_id") or ""),
        route_kind=route_kind,
        shadow_poolcheck_verdict=verdict,
        live_revalidation_required=True,
        source_preflight_complete=bool(source_simulation_ok and source_sellability_ok and source_atomic_profit),
    )
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _runtime.SiBot1ShadowRuntime._handle_trade = _trade
    _INSTALLED = True


install()
