from __future__ import annotations

"""Keep GPT/Base full-power discovery broad and fresh without a quote-call burst.

Starvation modes addressed here:
1. deterministic graph helpers repeatedly returned the same small route prefix;
2. the combined EVM scanner waits for every chain before its final shared publish;
3. a short-lived Base-only write to that shared file can be overwritten before the
   independent SiBot runtime reaches its next bounded poll;
4. the old equal per-chain budget spent scarce Base checks on CROSS_DEX_V2 routes,
   even though GPT's protected live bridge can execute only atomic single-router
   V2_CYCLE/V3_CYCLE routes;
5. even after a dedicated Base output existed, Base quotes were still produced only
   as part of the slow all-chain pass.

The patch rotates already-discovered route templates and reserves a fixed share of
THE SAME configured quote budget for Base. When the dedicated Base hot scanner is
enabled, the combined pass does not quote Base again; it only merges fresh rows from
the dedicated file. The Base worker uses a small rotating slice (default four route
checks per pass) so cadence improves without creating the old 24-check burst every
few seconds. Provider pressure is returned to the caller so the worker can back off.

The original observed_at_epoch is never rewritten. This module does not change
gross-profit rules, GPT's net-edge floor, PoolCheck/rug checks, liquidity/
sellability/slippage checks, wallet simulation, signing, broadcast, position limits,
LIVE/AUTO/ARMED controls, or trade sizing.
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
_DEFAULT_BASE_HOT_CHECKS = 4
_DEFAULT_BASE_HOT_ROUTES = 2


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


def _settings_and_budgets(app, ctxs):
    settings = _fp.load_kv_scoped(_fp.Path(app.csv_dir) / "auto_trading_settings.csv", 0)
    max_checks = max(10, min(500, _fp._int(settings.get("fast_market_max_candidate_checks", "60"), 60)))
    max_routes = max(1, min(100, _fp._int(settings.get("fast_market_max_routes_per_pass", "20"), 20)))
    check_budgets = _weighted_budgets(ctxs, max_checks, base_share=_BASE_CHECK_SHARE, minimum=5)
    route_budgets = _weighted_budgets(ctxs, max_routes, base_share=_BASE_ROUTE_SHARE, minimum=1)
    return settings, max_checks, max_routes, check_budgets, route_budgets


def _component_budgets(slug: str, per: int) -> tuple[int, int, int]:
    per = max(1, int(per))
    if slug == "base":
        if per == 1:
            return 1, 0, 0
        v2_budget = max(1, int(round(per * 0.50)))
        v3_budget = max(1, per - v2_budget)
        return v2_budget, v3_budget, 0

    v2_budget = max(1, int(per * 0.35))
    v3_budget = max(1, int(per * 0.50))
    cross_budget = max(0, per - v2_budget - v3_budget)
    while v2_budget + v3_budget + cross_budget > per:
        if cross_budget > 0:
            cross_budget -= 1
        elif v3_budget > 1:
            v3_budget -= 1
        elif v2_budget > 1:
            v2_budget -= 1
        else:
            break
    return v2_budget, v3_budget, cross_budget


def _scan_ctx(app, ctx, settings, checks_budget: int, routes_budget: int):
    slug = _slug(ctx)
    v2_budget, v3_budget, cross_budget = _component_budgets(slug, checks_budget)
    rows = []
    rejected = []
    if v2_budget > 0:
        r0, e0 = _fp._scan_v2_hot_chain(app, ctx, settings, v2_budget, routes_budget)
        rows.extend(r0); rejected.extend(e0)
    if v3_budget > 0:
        r1, e1 = _fp._scan_v3_chain(app, ctx, settings, v3_budget, routes_budget)
        rows.extend(r1); rejected.extend(e1)
    if cross_budget > 0:
        r2, e2 = _fp._scan_cross_v2_chain(app, ctx, settings, cross_budget)
        rows.extend(r2); rejected.extend(e2)
    return rows, rejected


def _route_score(row) -> Any:
    return _fp._dec(row.get("expected_gross_profit_base"), "0") - _fp._dec(
        row.get("slippage_reserve_base"), "0"
    )


def _provider_pressure_count(rejected) -> int:
    count = 0
    for row in rejected:
        text = str((row or {}).get("reason") or "").lower()
        if any(marker in text for marker in (
            "429", "rate limit", "too many requests", "quota exceeded",
            "compute units per second", "provider_rate_limit",
        )):
            count += 1
    return count


def scan_base_hot_routes(app, contexts):
    """Quote a small rotating Base-only slice and publish it immediately.

    The per-pass Base budget is intentionally much smaller than Base's old share of
    the all-chain pass. A faster cadence therefore improves freshness without turning
    provider pressure into an RPC storm.
    """
    ctxs = list(contexts)
    settings, _max_checks, _max_routes, check_budgets, route_budgets = _settings_and_budgets(app, ctxs)
    out = _fp.Path(app.csv_dir) / "auto" / "base_full_power_opportunities.csv"
    rej_path = _fp.Path(app.csv_dir) / "auto" / "base_full_power_rejections.csv"
    if not _fp._bool(settings.get("full_power_enabled", "true"), True) or not _fp._bool(
        settings.get("base_hot_scanner_enabled", "true"), True
    ):
        _fp._atomic_write(out, [], _fp.LIVE_HEADERS)
        return out, [], [], {"provider_pressure": 0, "checks_budget": 0, "routes_budget": 0}

    base = next((ctx for ctx in ctxs if _slug(ctx) == "base"), None)
    if base is None:
        _fp._atomic_write(out, [], _fp.LIVE_HEADERS)
        return out, [], [], {"provider_pressure": 0, "checks_budget": 0, "routes_budget": 0}

    allocated_checks = max(1, int(check_budgets.get("base", 1)))
    requested_checks = max(2, min(20, _fp._int(
        settings.get("base_hot_candidate_checks_per_pass", str(_DEFAULT_BASE_HOT_CHECKS)),
        _DEFAULT_BASE_HOT_CHECKS,
    )))
    checks_budget = min(allocated_checks, requested_checks)
    allocated_routes = max(1, int(route_budgets.get("base", 1)))
    requested_routes = max(1, min(10, _fp._int(
        settings.get("base_hot_max_routes_per_pass", str(_DEFAULT_BASE_HOT_ROUTES)),
        _DEFAULT_BASE_HOT_ROUTES,
    )))
    routes_budget = min(allocated_routes, requested_routes)

    try:
        rows, rejected = _scan_ctx(app, base, settings, checks_budget, routes_budget)
    except Exception as exc:
        rows = []
        rejected = [{
            "observed_at_epoch": int(_fp.time.time()),
            "chain_id": getattr(base.config, "chain_id", 8453),
            "chain_slug": "base",
            "route_kind": "BASE_HOT",
            "route_path": "",
            "stage": "thread",
            "reason": f"{type(exc).__name__}:{exc}",
        }]

    rows = sorted(list(rows), key=_route_score, reverse=True)[:routes_budget]
    # Never rewrite observed_at_epoch: GPT's normal freshness gate stays authoritative.
    _fp._atomic_write(out, rows, _fp.LIVE_HEADERS)
    _fp._atomic_rows(rej_path, list(rejected)[-300:], _fp.POWER_REJECT_HEADERS)
    return out, rows, rejected, {
        "provider_pressure": _provider_pressure_count(rejected),
        "checks_budget": checks_budget,
        "routes_budget": routes_budget,
    }


def _fresh_base_rows(base_out, settings) -> list[dict]:
    max_age = max(5, min(60, _fp._int(settings.get("base_hot_feed_merge_max_age_seconds", "20"), 20)))
    now = int(_fp.time.time())
    out = []
    for row in _fp._rows(base_out):
        try:
            observed = int(float(row.get("observed_at_epoch") or 0))
        except Exception:
            observed = 0
        if observed > 0 and 0 <= now - observed <= max_age:
            out.append(row)
    return out


def _scan_full_power_hot_routes(app, contexts):
    """Scan the fixed budget; dedicated Base mode never double-quotes Base."""
    ctxs = list(contexts)
    settings, _max_checks, max_routes, check_budgets, route_budgets = _settings_and_budgets(app, ctxs)
    out = _fp.Path(app.csv_dir) / "auto" / "full_power_opportunities.csv"
    base_out = _fp.Path(app.csv_dir) / "auto" / "base_full_power_opportunities.csv"
    rej_path = _fp.Path(app.csv_dir) / "auto" / "full_power_rejections.csv"
    if not _fp._bool(settings.get("full_power_enabled", "true"), True):
        return out, [], []

    dedicated_base = _fp._bool(settings.get("base_hot_scanner_enabled", "true"), True)
    rows = []
    rejected = []
    scan_ctxs = [ctx for ctx in ctxs if not (dedicated_base and _slug(ctx) == "base")]

    workers = max(1, min(max(1, len(scan_ctxs)), _fp._int(settings.get("full_power_parallel_chains", "5"), 5)))
    if scan_ctxs:
        with _fp.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="power-chain") as ex:
            futures = {
                ex.submit(
                    _scan_ctx,
                    app,
                    ctx,
                    settings,
                    max(1, int(check_budgets.get(_slug(ctx), 1))),
                    max(1, int(route_budgets.get(_slug(ctx), 1))),
                ): ctx
                for ctx in scan_ctxs
            }
            for future in _fp.as_completed(futures):
                ctx = futures[future]
                try:
                    r, e = future.result()
                    rows.extend(r)
                    rejected.extend(e)
                    if _slug(ctx) == "base":
                        base_rows = sorted(list(r), key=_route_score, reverse=True)[:max_routes]
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

    if dedicated_base:
        # Merge only genuinely fresh rows. The dedicated file itself is left intact
        # for GPT, which applies its own stricter freshness/economic checks.
        rows.extend(_fresh_base_rows(base_out, settings))

    rows.sort(key=_route_score, reverse=True)
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
        "base_double_quote=false base_hot_checks_default=4 "
        "freshness_unchanged=true safety_unchanged=true",
        flush=True,
    )


install()
