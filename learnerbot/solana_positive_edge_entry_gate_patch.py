from __future__ import annotations

import time
from contextlib import closing
from decimal import Decimal

from . import solana_sibot as _sol
from . import solana_profit_guard_patch as _guard
from . import solana_partial_sell_profit_guard_patch as _partial_guard

# Final BUY-side profitability gate.  A leader can have a positive PF yet still
# have returns too small for a 0.0005 SOL follower after fixed Solana costs.
_sol.DEFAULTS.update({
    "live_min_leader_median_return_pct": (
        "5.0",
        "Minimum median reconstructed leader return percent before a LIVE copied BUY",
    ),
    "live_min_leader_recent_median_return_pct": (
        "4.0",
        "Minimum median return percent across the leader's recent closed trades before LIVE copy",
    ),
    "live_edge_recent_trade_window": (
        "10",
        "Recent reconstructed leader trades used by the positive-edge entry gate",
    ),
    "live_quarantine_after_first_copied_loss_minutes": (
        "360",
        "Quarantine a leader after the first realised losing LIVE copy",
    ),
})

_PREV_PROCESS = _sol.process_leader_event
_PREV_COPIED_OK = _guard._copied_ok


def _d(v, default="0") -> Decimal:
    return _sol._dec(v, default)


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / Decimal(2)


def leader_return_edge(app, wallet: str, cfg: dict) -> dict:
    lookback = max(1, min(365, _sol._int(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    recent_n = max(3, min(50, _sol._int(cfg.get("live_edge_recent_trade_window"), 10)))
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT cost_sol,net_sol,sell_ts FROM trades WHERE wallet=? AND sell_ts>=? ORDER BY sell_ts",
            (str(wallet), cutoff),
        ).fetchall()]

    returns = []
    for row in rows:
        cost = _d(row.get("cost_sol"), 0)
        net = _d(row.get("net_sol"), 0)
        if cost <= 0:
            continue
        pct = net * Decimal(100) / cost
        # Bound pathological reconstruction artefacts without hiding genuine losses.
        returns.append(max(Decimal("-95"), min(Decimal("500"), pct)))

    recent = returns[-recent_n:]
    positive = [x for x in returns if x > 0]
    return {
        "closed": len(returns),
        "median_return_pct": _median(returns),
        "recent_closed": len(recent),
        "recent_median_return_pct": _median(recent),
        "median_positive_return_pct": _median(positive),
    }


def _edge_ok(app, wallet: str, cfg: dict) -> tuple[bool, str, dict]:
    metrics = leader_return_edge(app, wallet, cfg)
    min_closed = max(3, _sol._int(cfg.get("min_closed_trades"), 5))
    if int(metrics["closed"]) < min_closed:
        return False, f"leader has only {metrics['closed']} return samples; need {min_closed}", metrics

    historical_floor = max(Decimal(0), _d(cfg.get("live_min_leader_median_return_pct"), "5"))
    recent_floor = max(Decimal(0), _d(cfg.get("live_min_leader_recent_median_return_pct"), "4"))
    historical = _d(metrics.get("median_return_pct"), 0)
    recent = _d(metrics.get("recent_median_return_pct"), 0)

    if historical < historical_floor:
        return False, f"leader median return {historical:.3f}% is below LIVE edge floor {historical_floor:.3f}%", metrics
    if recent < recent_floor:
        return False, f"leader recent median return {recent:.3f}% is below LIVE edge floor {recent_floor:.3f}%", metrics
    return True, "ok", metrics


def _latest_copied_result(app, tid: str, wallet: str):
    try:
        with closing(_sol.connect(app)) as conn:
            row = conn.execute(
                """SELECT realised_net_sol,closed_at FROM positions
                   WHERE telegram_id=? AND leader_wallet=? AND status='CLOSED' AND mode='LIVE'
                   ORDER BY closed_at DESC LIMIT 1""",
                (str(tid), str(wallet)),
            ).fetchone()
        if not row:
            return None
        return _d(row["realised_net_sol"], 0), int(row["closed_at"] or 0)
    except Exception:
        return None


def copied_ok_quarantine_first_loss(app, tid, wallet, cfg):
    latest = _latest_copied_result(app, tid, wallet)
    if latest:
        net, closed_at = latest
        cooldown = max(5, _sol._int(cfg.get("live_quarantine_after_first_copied_loss_minutes"), 360)) * 60
        if net < 0 and int(time.time()) < int(closed_at) + cooldown:
            return False
    return _PREV_COPIED_OK(app, tid, wallet, cfg)


def process_leader_event_positive_edge(app, event: dict):
    """Reject LIVE BUY signals whose leader history cannot clear follower costs."""
    if str((event or {}).get("action") or "").upper() != "BUY":
        return _PREV_PROCESS(app, event)

    cfg = _sol.settings(app)
    wallet = str((event or {}).get("leader_wallet") or "")
    if not wallet:
        return [{"action": "REJECT", "reason": "leader wallet missing from BUY signal"}]

    ok, reason, metrics = _edge_ok(app, wallet, cfg)
    if not ok:
        return [{
            "action": "REJECT",
            "reason": "POSITIVE_EDGE_GATE: " + reason,
            "leader_wallet": wallet,
            "median_return_pct": str(metrics.get("median_return_pct")),
            "recent_median_return_pct": str(metrics.get("recent_median_return_pct")),
        }]
    return _PREV_PROCESS(app, event)


def install():
    if getattr(_sol, "_positive_edge_entry_gate_installed", False):
        return
    _guard._copied_ok = copied_ok_quarantine_first_loss
    _sol.process_leader_event = process_leader_event_positive_edge
    _sol._positive_edge_entry_gate_installed = True
    print(
        "[solana-positive-edge-entry] historical_median>=5% recent_median>=4% "
        "first_copied_loss_quarantine=6h"
    )


install()
