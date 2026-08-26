from __future__ import annotations

"""Keep GPT/Base full-power discovery broad and fresh without more quote calls.

Two starvation modes are addressed:
1. deterministic graph helpers repeatedly returned the same small route prefix;
2. the combined EVM scanner waited for every chain before publishing Base rows,
   so a valid Base quote could age past GPT's 15-second nomination window while
   an unrelated EVM chain was still scanning.

This patch rotates already-discovered route templates while preserving the existing
quote-call budget, and atomically publishes the Base result as soon as its existing
scan finishes. It never rewrites the row's original observed_at_epoch, so quote
freshness is not fabricated. The normal combined file is still written at the end
of the pass.

It does not change gross-profit rules, GPT's net-edge floor, PoolCheck/rug checks,
liquidity/sellability/slippage checks, wallet simulation, signing, broadcast,
position limits, LIVE/AUTO/ARMED controls, or trade sizing.
"""

import threading
from typing import Any

from . import full_power_scanner as _fp

_ORIGINAL_GRAPH_TRIANGLES = _fp._graph_triangles
_ORIGINAL_V3_TRIANGLES = _fp._v3_triangles
_LOCK = threading.RLock()
_CURSOR: dict[tuple[str, ...], int] = {}
_MAX_EXPLORATION = 1000
_EXPANSION_FACTOR = 12


def _rotate(key: tuple[str, ...], items: list[Any], take: int) -> list[Any]:
    if not items or take <= 0:
        return []
    n = len(items)
    take = min(int(take), n)
    with _LOCK:
        start = int(_CURSOR.get(key, 0)) % n
        _CURSOR[key] = (start + take) % n
    return [items[(start + idx) % n] for idx in range(take)]


def _rotating_graph_triangles(
    pool_rows,
    chain_id: int,
    factory_address: str,
    wrapped: str,
    token_universe,
    max_checks: int,
):
    take = max(1, int(max_checks))
    explore = min(_MAX_EXPLORATION, max(take, take * _EXPANSION_FACTOR))
    candidates = _ORIGINAL_GRAPH_TRIANGLES(
        pool_rows,
        chain_id,
        factory_address,
        wrapped,
        token_universe,
        explore,
    )
    key = ("v2", str(chain_id), str(factory_address or "").lower())
    return _rotate(key, list(candidates), take)


def _rotating_v3_triangles(pool_rows, wrapped: str, max_paths: int):
    take = max(1, int(max_paths))
    explore = min(_MAX_EXPLORATION, max(take, take * _EXPANSION_FACTOR))
    candidates = _ORIGINAL_V3_TRIANGLES(pool_rows, wrapped, explore)
    first = pool_rows[0] if pool_rows else {}
    key = (
        "v3",
        str(first.get("chain_id") or ""),
        str(first.get("factory_address") or "").lower(),
        str(wrapped or "").lower(),
    )
    return _rotate(key, list(candidates), take)


def _scan_full_power_hot_routes(app, contexts):
    """Mirror the existing scanner but publish Base immediately on completion.

    No additional chain scan or quote is performed. The partial Base write uses the
    same atomic CSV path that GPT already consumes. The final combined write remains
    authoritative for the normal fast-market loop.
    """
    settings = _fp.load_kv_scoped(_fp.Path(app.csv_dir) / "auto_trading_settings.csv", 0)
    out = _fp.Path(app.csv_dir) / "auto" / "full_power_opportunities.csv"
    rej_path = _fp.Path(app.csv_dir) / "auto" / "full_power_rejections.csv"
    if not _fp._bool(settings.get("full_power_enabled", "true"), True):
        return out, [], []

    max_checks = max(10, min(500, _fp._int(settings.get("fast_market_max_candidate_checks", "60"), 60)))
    max_routes = max(1, min(100, _fp._int(settings.get("fast_market_max_routes_per_pass", "20"), 20)))
    ctxs = list(contexts)
    per = max(5, max_checks // max(1, len(ctxs)))
    rows = []
    rejected = []

    def scan_one(ctx):
        v2_budget = max(2, int(per * 0.35))
        v3_budget = max(2, int(per * 0.50))
        cross_budget = max(1, per - v2_budget - v3_budget)
        route_budget = max(1, max_routes // max(1, len(ctxs)))
        r0, e0 = _fp._scan_v2_hot_chain(app, ctx, settings, v2_budget, route_budget)
        r1, e1 = _fp._scan_v3_chain(app, ctx, settings, v3_budget, route_budget)
        r2, e2 = _fp._scan_cross_v2_chain(app, ctx, settings, cross_budget)
        return r0 + r1 + r2, e0 + e1 + e2

    workers = max(1, min(len(ctxs), _fp._int(settings.get("full_power_parallel_chains", "5"), 5)))
    with _fp.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="power-chain") as ex:
        futures = {ex.submit(scan_one, ctx): ctx for ctx in ctxs}
        for future in _fp.as_completed(futures):
            ctx = futures[future]
            try:
                r, e = future.result()
                rows.extend(r)
                rejected.extend(e)
                if str(getattr(ctx.config, "slug", "")).strip().lower() == "base":
                    base_rows = sorted(
                        list(r),
                        key=lambda row: _fp._dec(row.get("expected_gross_profit_base"), "0")
                        - _fp._dec(row.get("slippage_reserve_base"), "0"),
                        reverse=True,
                    )[:max_routes]
                    # Important: observed_at_epoch is left untouched. If the Base
                    # scan itself is too slow, GPT will still reject the stale row.
                    _fp._atomic_write(out, base_rows, _fp.LIVE_HEADERS)
            except Exception as exc:
                rejected.append({
                    "observed_at_epoch": int(_fp.time.time()),
                    "chain_id": "",
                    "chain_slug": str(getattr(ctx.config, "slug", "")),
                    "route_kind": "FULL_POWER",
                    "route_path": "",
                    "stage": "thread",
                    "reason": f"{type(exc).__name__}:{exc}",
                })
                if str(getattr(ctx.config, "slug", "")).strip().lower() == "base":
                    _fp._atomic_write(out, [], _fp.LIVE_HEADERS)

    rows.sort(
        key=lambda row: _fp._dec(row.get("expected_gross_profit_base"), "0")
        - _fp._dec(row.get("slippage_reserve_base"), "0"),
        reverse=True,
    )
    rows = rows[:max_routes]
    _fp._atomic_write(out, rows, _fp.LIVE_HEADERS)
    _fp._atomic_rows(rej_path, rejected[-1500:], _fp.POWER_REJECT_HEADERS)
    return out, rows, rejected


def install() -> None:
    if getattr(_fp, "_candidate_rotation_installed", False):
        return
    _fp._graph_triangles = _rotating_graph_triangles
    _fp._v3_triangles = _rotating_v3_triangles
    _fp.scan_full_power_hot_routes = _scan_full_power_hot_routes
    _fp._candidate_rotation_installed = True
    print(
        "[full-power-candidate-rotation] installed=true rotating=true "
        "base_early_publish=true quote_budget_unchanged=true freshness_unchanged=true "
        "safety_unchanged=true",
        flush=True,
    )


install()
