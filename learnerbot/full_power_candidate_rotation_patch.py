from __future__ import annotations

"""Keep GPT/Base full-power discovery broad and fresh without more quote calls.

Starvation modes addressed here:
1. deterministic graph helpers repeatedly returned the same small route prefix;
2. the combined EVM scanner waits for every chain before its final shared publish;
3. a short-lived Base-only write to that shared file can be overwritten before the
   independent SiBot runtime reaches its next bounded poll;
4. the old equal per-chain budget spent scarce Base checks on CROSS_DEX_V2 routes,
   even though GPT's protected live bridge can execute only atomic single-router
   V2_CYCLE/V3_CYCLE routes.

The patch rotates already-discovered route templates, gives Base a larger share of
THE SAME total quote-call budget, and uses Base's entire allocation on executable
V2/V3 cycle discovery. Other EVM chains retain bounded cross-DEX research. Base is
atomically published to a dedicated read-only feed as soon as its existing scan
finishes. The original observed_at_epoch is never rewritten, so quote freshness is
not fabricated.

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
_BASE_CHECK_SHARE = 0.40
_BASE_ROUTE_SHARE = 0.40


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


def _slug(ctx) -> str:
    return str(getattr(ctx.config, "slug", "") or "").strip().lower()


def _weighted_budgets(ctxs, total: int, *, base_share: float, minimum: int) -> dict[str, int]:
    """Allocate a fixed total budget, favouring Base without increasing total work."""
    ctxs = list(ctxs)
    if not ctxs:
        return {}
    total = max(len(ctxs), int(total))
    minimum = max(1, int(minimum))
    slugs = [_slug(ctx) for ctx in ctxs]
    budgets = {slug: 0 for slug in slugs}
    base_present = "base" in budgets

    # Reserve a small fair-share floor for every chain first. If the configured
    # total is unusually small, fall back to an even bounded distribution.
    floor_total = minimum * len(slugs)
    if total < floor_total:
        q, r = divmod(total, len(slugs))
        for idx, slug in enumerate(slugs):
            budgets[slug] = q + (1 if idx < r else 0)
        return budgets

    for slug in slugs:
        budgets[slug] = minimum
    remaining = total - floor_total

    if base_present and remaining > 0:
        desired_base = max(minimum, int(round(total * max(0.0, min(0.80, base_share)))))
        extra_base = min(remaining, max(0, desired_base - budgets["base"]))
        budgets["base"] += extra_base
        remaining -= extra_base

    others = [slug for slug in slugs if slug != "base"] if base_present else list(slugs)
    if not others:
        budgets["base"] += remaining
        return budgets
    q, r = divmod(remaining, len(others))
    for idx, slug in enumerate(others):
        budgets[slug] += q + (1 if idx < r else 0)
    return budgets


def _scan_full_power_hot_routes(app, contexts):
    """Scan the existing fixed budget and persist Base immediately on completion."""
    settings = _fp.load_kv_scoped(_fp.Path(app.csv_dir) / "auto_trading_settings.csv", 0)
    out = _fp.Path(app.csv_dir) / "auto" / "full_power_opportunities.csv"
    base_out = _fp.Path(app.csv_dir) / "auto" / "base_full_power_opportunities.csv"
    rej_path = _fp.Path(app.csv_dir) / "auto" / "full_power_rejections.csv"
    if not _fp._bool(settings.get("full_power_enabled", "true"), True):
        return out, [], []

    max_checks = max(10, min(500, _fp._int(settings.get("fast_market_max_candidate_checks", "60"), 60)))
    max_routes = max(1, min(100, _fp._int(settings.get("fast_market_max_routes_per_pass", "20"), 20)))
    ctxs = list(contexts)
    check_budgets = _weighted_budgets(ctxs, max_checks, base_share=_BASE_CHECK_SHARE, minimum=5)
    route_budgets = _weighted_budgets(ctxs, max_routes, base_share=_BASE_ROUTE_SHARE, minimum=1)
    rows = []
    rejected = []

    def scan_one(ctx):
        slug = _slug(ctx)
        per = max(1, int(check_budgets.get(slug, 1)))
        route_budget = max(1, int(route_budgets.get(slug, 1)))

        if slug == "base":
            # GPT's protected live executor can use only atomic single-router V2/V3
            # cycles. Do not spend Base's fixed budget on cross-DEX research rows.
            v2_budget = max(2, int(round(per * 0.45)))
            v3_budget = max(2, per - v2_budget)
            cross_budget = 0
        else:
            v2_budget = max(2, int(per * 0.35))
            v3_budget = max(2, int(per * 0.50))
            cross_budget = max(0, per - v2_budget - v3_budget)

        # Keep the sum bounded if minimum component floors exceeded a tiny budget.
        if v2_budget + v3_budget + cross_budget > per:
            overflow = v2_budget + v3_budget + cross_budget - per
            if cross_budget:
                cut = min(cross_budget, overflow)
                cross_budget -= cut
                overflow -= cut
            if overflow and v3_budget > 1:
                cut = min(v3_budget - 1, overflow)
                v3_budget -= cut
                overflow -= cut
            if overflow and v2_budget > 1:
                v2_budget = max(1, v2_budget - overflow)

        r0, e0 = _fp._scan_v2_hot_chain(app, ctx, settings, v2_budget, route_budget)
        r1, e1 = _fp._scan_v3_chain(app, ctx, settings, v3_budget, route_budget)
        if cross_budget > 0:
            r2, e2 = _fp._scan_cross_v2_chain(app, ctx, settings, cross_budget)
        else:
            r2, e2 = [], []
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
                if _slug(ctx) == "base":
                    base_rows = sorted(
                        list(r),
                        key=lambda row: _fp._dec(row.get("expected_gross_profit_base"), "0")
                        - _fp._dec(row.get("slippage_reserve_base"), "0"),
                        reverse=True,
                    )[:max_routes]
                    # observed_at_epoch is left untouched. If the Base scan itself
                    # is slow, GPT still rejects stale evidence.
                    _fp._atomic_write(base_out, base_rows, _fp.LIVE_HEADERS)
            except Exception as exc:
                rejected.append({
                    "observed_at_epoch": int(_fp.time.time()),
                    "chain_id": "",
                    "chain_slug": _slug(ctx),
                    "route_kind": "FULL_POWER",
                    "route_path": "",
                    "stage": "thread",
                    "reason": f"{type(exc).__name__}:{exc}",
                })
                if _slug(ctx) == "base":
                    _fp._atomic_write(base_out, [], _fp.LIVE_HEADERS)

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
        "base_dedicated_feed=true base_budget_weighted=true base_cross_dex_budget=0 "
        "quote_budget_unchanged=true freshness_unchanged=true safety_unchanged=true",
        flush=True,
    )


install()
