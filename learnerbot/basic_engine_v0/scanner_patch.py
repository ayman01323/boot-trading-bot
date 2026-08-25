from __future__ import annotations

"""Candidate-generation fixes for the Basic Engine v0 main path.

This module changes discovery/orchestration only.  It does not bypass execution
quarantine, price-impact/liquidity checks, wallet simulation, pool-rug checks,
minimum retained profit, signing controls, or the final pre-broadcast eth_call.
"""

import time
from pathlib import Path

from .. import full_power_scanner as _full
from ..live_executor import LiveTrader

_ORIGINAL_SCAN_V3 = _full._scan_v3_chain
_INSTALLED = False


def discover_full_power_pools_v0(app, contexts, *, include_v3: bool = True) -> dict:
    """Always refresh the V2 graph; make V3 discovery independently optional."""
    settings = _full.load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", 0)
    now = int(time.time())
    found = 0
    v2_seen = 0
    rejected = []

    for ctx in contexts:
        # V2 is a first-class execution path and must not depend on V3 being on.
        for venue in _full._venues(app, ctx.config.chain_id, "V2"):
            try:
                trader = LiveTrader(
                    app,
                    ctx.config.slug,
                    require_wallet=False,
                    router_override=venue["router"],
                )
                before = len(_full._rows(Path(app.csv_dir) / "auto" / "pool_registry.csv"))
                pools = _full._crawl_factory_pairs(
                    trader,
                    venue["factory"],
                    app,
                    settings,
                    now,
                    dex_name=venue.get("dex_name") or "V2",
                    per_cycle_override=max(
                        1,
                        min(
                            50,
                            _full._int(
                                settings.get("full_power_v2_discovery_pairs_per_venue", "8"),
                                8,
                            ),
                        ),
                    ),
                )
                seed_settings = dict(settings)
                seed_settings["direct_market_seed_pair_checks_per_venue"] = str(
                    max(
                        0,
                        min(
                            100,
                            _full._int(settings.get("full_power_v2_seed_pair_checks", "18"), 18),
                        ),
                    )
                )
                pools = _full._seed_factory_pairs(
                    trader,
                    venue["factory"],
                    venue.get("dex_name") or "V2",
                    app,
                    seed_settings,
                    pools,
                    now,
                )
                v2_seen += max(0, len(pools) - before)
            except Exception as exc:
                rejected.append(
                    {
                        "observed_at_epoch": now,
                        "chain_id": ctx.config.chain_id,
                        "chain_slug": ctx.config.slug,
                        "route_kind": "V2_DISCOVERY",
                        "route_path": "",
                        "stage": "venue",
                        "reason": f"{venue.get('dex_name')}:{type(exc).__name__}:{exc}",
                    }
                )

        if include_v3:
            rows, rej = _full.discover_v3_seed_pools_for_context(app, ctx, settings, now)
            found += len(rows)
            rejected.extend(rej)

    if rejected:
        _full._atomic_rows(
            Path(app.csv_dir) / "auto" / "power_discovery_rejections.csv",
            rejected[-500:],
            _full.POWER_REJECT_HEADERS,
        )
    return {"v3_pools_seen": found, "v2_pools_added": v2_seen, "rejected": len(rejected)}


def scan_v3_chain_v0(app, ctx, settings, checks_budget: int, routes_budget: int):
    """Make the V3 hot scanner obey the same switch as V3 discovery."""
    if not _full._bool(settings.get("v3_scanner_enabled", "true"), True):
        return [], []
    return _ORIGINAL_SCAN_V3(app, ctx, settings, checks_budget, routes_budget)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _full.discover_full_power_pools = discover_full_power_pools_v0
    _full._scan_v3_chain = scan_v3_chain_v0
    _full._basic_engine_v0_scanner_patch_installed = True
    _INSTALLED = True


install()
