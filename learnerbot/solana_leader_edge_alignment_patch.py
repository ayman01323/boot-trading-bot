from __future__ import annotations

from decimal import Decimal

from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol


_PREV_QUALITY_METRICS = _guard.quality_metrics
_PREV_HISTORICAL_OK = _guard._historical_ok


def quality_metrics(app, wallet, cfg):
    """Extend selector evidence with the exact median-return metrics used by LIVE."""
    out = dict(_PREV_QUALITY_METRICS(app, wallet, cfg))
    edge = _edge.leader_return_edge(app, wallet, cfg)
    out["edge_closed"] = int(edge.get("closed") or 0)
    out["median_return_pct"] = _sol._dec(edge.get("median_return_pct"), 0)
    out["recent_edge_closed"] = int(edge.get("recent_closed") or 0)
    out["recent_median_return_pct"] = _sol._dec(edge.get("recent_median_return_pct"), 0)
    return out


def historical_ok(metrics, cfg):
    """Require selector candidates to meet the same edge floors as LIVE BUY preflight."""
    if not _PREV_HISTORICAL_OK(metrics, cfg):
        return False

    historical_floor = max(
        Decimal(0),
        _sol._dec(cfg.get("live_min_leader_median_return_pct"), "5"),
    )
    recent_floor = max(
        Decimal(0),
        _sol._dec(cfg.get("live_min_leader_recent_median_return_pct"), "4"),
    )
    historical = _sol._dec(metrics.get("median_return_pct"), 0)
    recent = _sol._dec(metrics.get("recent_median_return_pct"), 0)
    return historical >= historical_floor and recent >= recent_floor


def install() -> None:
    if getattr(_guard, "_leader_edge_alignment_installed", False):
        return
    # solana_profit_guard_patch.refresh_rankings resolves these module globals at
    # runtime, so its existing ranking/copy-performance logic stays intact while
    # the qualified pool is narrowed to candidates the LIVE gate can actually use.
    _guard.quality_metrics = quality_metrics
    _guard._historical_ok = historical_ok
    _guard._leader_edge_alignment_installed = True
    print(
        "[solana-leader-edge-alignment] selector_matches_live_edge=true "
        "thresholds=unchanged"
    )


install()
