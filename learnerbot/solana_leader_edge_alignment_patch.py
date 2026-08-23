from __future__ import annotations

import time
from contextlib import closing
from decimal import Decimal

from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol


_PREV_QUALITY_METRICS = _guard.quality_metrics
_PREV_HISTORICAL_OK = _guard._historical_ok
_PREV_REFRESH = _sol.refresh_rankings


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


def _broad_positive_candidates(app, cfg):
    """Return a bounded pool of profitable reconstructed wallets, not only Top-20.

    The public/research Top-20 remains produced by the original ranking function.
    This pool exists only so strict LIVE gates are applied *before* the final leader
    slots are chosen.  Quality thresholds are unchanged and are still evaluated by
    quality_metrics()/historical_ok() below.
    """
    lookback = max(1, min(365, _sol._int(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    cap = max(20, min(1000, _sol._int(cfg.get("leader_selection_candidate_cap"), 500)))
    with closing(_sol.connect(app)) as conn:
        rows = conn.execute(
            """SELECT wallet,
                      SUM(CAST(net_sol AS REAL)) AS net,
                      COUNT(*) AS closed
               FROM trades
               WHERE sell_ts>=?
               GROUP BY wallet
               HAVING SUM(CAST(net_sol AS REAL)) > 0
               ORDER BY net DESC, closed DESC
               LIMIT ?""",
            (cutoff, cap),
        ).fetchall()
    return [
        {
            "wallet": str(r["wallet"]),
            "net": _sol._dec(r["net"], 0),
            "closed": int(r["closed"] or 0),
        }
        for r in rows
        if str(r["wallet"] or "")
    ]


def _qualified_candidates(app, cfg, candidates):
    """Apply the exact existing LIVE-aligned quality gates to every candidate."""
    qualified = []
    for candidate in candidates:
        wallet = str(candidate.get("wallet") or "")
        if not wallet:
            continue
        metrics = quality_metrics(app, wallet, cfg)
        if historical_ok(metrics, cfg):
            qualified.append((candidate, metrics))
    return qualified


def refresh_rankings(app, telegram_id=None):
    """Keep Top-20 reporting, but search beyond it for genuinely qualified leaders.

    Previously the broad profitability ranking was truncated to Top-20 first and
    the stricter PF/win-rate/drawdown/recent/median-return gates were applied only
    afterwards.  Twenty failures therefore produced zero leaders even when a wallet
    outside that display shortlist could satisfy every LIVE gate.  This function
    preserves the original Top-20 output, then evaluates a bounded broader pool with
    the same unchanged gates before filling leader slots.
    """
    top = _PREV_REFRESH(app, telegram_id)
    cfg = _sol.settings(app)

    try:
        candidates = _broad_positive_candidates(app, cfg)
        qualified = _qualified_candidates(app, cfg, candidates)
    except Exception as exc:
        print(
            "[solana-leader-edge-alignment] broader_search_failed="
            f"{type(exc).__name__}: {str(exc)[:180]}"
        )
        return top

    now = int(time.time())
    activity_hours = max(1, min(168, _sol._int(cfg.get("leader_recent_activity_hours"), 6)))
    activity_cutoff = now - activity_hours * 3600
    qualified.sort(
        key=lambda x: (
            1 if int(x[1].get("last_activity_ts") or 0) >= activity_cutoff else 0,
            x[1]["profit_factor"],
            x[1]["net"],
            x[1]["win_rate"],
            -x[1]["drawdown_pct"],
            int(x[1].get("last_activity_ts") or 0),
        ),
        reverse=True,
    )

    users = [
        u
        for u in _sol.all_users(app.csv_dir, enabled_only=True)
        if str(u.get("status") or "").upper() == "ACTIVE"
    ]
    if telegram_id is not None:
        users = [u for u in users if str(u.get("telegram_id")) == str(telegram_id)]
    leaders_n = max(1, min(10, _sol._int(cfg.get("leaders_per_user"), 3)))

    selected = {}
    for user in users:
        tid = str(user.get("telegram_id") or "")
        choices = []
        for candidate, metrics in qualified:
            wallet = str(candidate.get("wallet") or "")
            if _guard._copied_ok(app, tid, wallet, cfg):
                choices.append((wallet, metrics))
            if len(choices) >= leaders_n:
                break
        selected[tid] = choices

    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        for user in users:
            tid = str(user.get("telegram_id") or "")
            old = {
                str(r["wallet"]): int(r["selected_at"] or now)
                for r in conn.execute(
                    "SELECT wallet,selected_at FROM leaders WHERE telegram_id=?",
                    (tid,),
                ).fetchall()
            }
            conn.execute("DELETE FROM leaders WHERE telegram_id=?", (tid,))
            for rank, (wallet, metrics) in enumerate(selected.get(tid, []), 1):
                conn.execute(
                    """INSERT INTO leaders(
                           telegram_id,rank,wallet,net_profit_sol,win_rate,
                           closed_trades,selected_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        tid,
                        rank,
                        wallet,
                        str(metrics["net"]),
                        float(metrics["win_rate"]),
                        int(metrics["closed"]),
                        old.get(wallet, now),
                        now,
                    ),
                )
        conn.commit()

    _sol.export_csv(app)
    total_selected = sum(len(v) for v in selected.values())
    print(
        "[solana-leader-edge-alignment] broader_pool=%d qualified=%d selected=%d "
        "thresholds=unchanged"
        % (len(candidates), len(qualified), total_selected)
    )
    return top


def install() -> None:
    if getattr(_guard, "_leader_edge_alignment_installed", False):
        return
    # solana_profit_guard_patch.refresh_rankings resolves these module globals at
    # runtime, so its existing ranking/copy-performance logic stays intact while
    # the qualified pool is narrowed to candidates the LIVE gate can actually use.
    _guard.quality_metrics = quality_metrics
    _guard._historical_ok = historical_ok
    # Preserve the Top-20 research list but do not restrict LIVE leader discovery
    # to those 20 rows before the stricter quality/edge checks have run.
    _sol.refresh_rankings = refresh_rankings
    _guard._leader_edge_alignment_installed = True
    print(
        "[solana-leader-edge-alignment] selector_matches_live_edge=true "
        "broader_qualified_search=true thresholds=unchanged"
    )


install()
