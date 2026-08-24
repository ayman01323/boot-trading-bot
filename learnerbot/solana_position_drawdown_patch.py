from __future__ import annotations

import time
from contextlib import closing
from decimal import Decimal

from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol

# Preserve the fragment-level metric for diagnostics while making the production
# leader-quality drawdown gate use the same economic-position grouping already
# used for win rate and median return.
_PREV_QUALITY_METRICS = _guard.quality_metrics
_FRAGMENT_DRAWDOWN = _guard._drawdown


def _position_drawdown(rows) -> Decimal:
    """Maximum peak-to-trough equity drawdown across closed positions.

    The Solana history matcher can emit several FIFO rows for one scale-in/scale-out
    decision. Treating those rows as independent equity steps can create a large
    artificial drawdown inside a position that ultimately closed profitably. The
    existing position bucketing is reused here so the drawdown denominator matches
    the win-rate and median-return semantics.
    """
    positions = _guard._bucket_positions(rows)
    equity = Decimal(1)
    peak = Decimal(1)
    worst = Decimal(0)
    for position in positions:
        cost = sum((_sol._dec(r.get("cost_sol"), 0) for r in position), Decimal(0))
        net = sum((_sol._dec(r.get("net_sol"), 0) for r in position), Decimal(0))
        if cost <= 0:
            continue
        ret = max(Decimal("-0.95"), min(Decimal("5"), net / cost))
        equity *= Decimal(1) + ret
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * Decimal(100))
    return worst


def quality_metrics_position_drawdown(app, wallet, cfg):
    out = dict(_PREV_QUALITY_METRICS(app, wallet, cfg))
    lookback = max(1, min(365, _sol._int(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    with closing(_sol.connect(app)) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT mint,buy_ts,cost_sol,net_sol,sell_ts "
                "FROM trades WHERE wallet=? AND sell_ts>=? ORDER BY sell_ts",
                (str(wallet), cutoff),
            ).fetchall()
        ]

    # Keep both views visible. Only drawdown_pct is consumed by the existing
    # historical quality gate; the configured cap itself is unchanged.
    out["fragment_drawdown_pct"] = _FRAGMENT_DRAWDOWN(rows)
    out["drawdown_pct"] = _position_drawdown(rows)
    return out


def install() -> None:
    if getattr(_guard, "_position_drawdown_patch_installed", False):
        return
    _guard.quality_metrics = quality_metrics_position_drawdown
    _guard._position_drawdown_patch_installed = True
    print(
        "[solana-position-drawdown] position_level=true "
        "fragment_metric_retained=true thresholds=unchanged"
    )


install()
