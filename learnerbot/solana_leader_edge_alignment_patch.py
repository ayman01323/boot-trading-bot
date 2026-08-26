from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol


_PREV_QUALITY_METRICS = _guard.quality_metrics
_PREV_HISTORICAL_OK = _guard._historical_ok
_PREV_REFRESH = _sol.refresh_rankings
_BRIDGE = Path("/var/tmp/boot/solana_leader_selector.json")
_BRIDGE_LOCK = threading.Lock()


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


def _quality_failure_reason(metrics: dict, cfg: dict) -> str:
    """First failing gate, in the same order as the active selector."""
    if _sol._bool(cfg.get("require_complete_history"), True) and not metrics.get("history_complete"):
        return "history incomplete"
    if int(metrics.get("closed") or 0) < max(1, _sol._int(cfg.get("min_closed_trades"), 10)):
        return "not enough closed trades"
    if _sol._dec(metrics.get("win_rate"), 0) < _sol._dec(cfg.get("min_win_rate_pct"), 65):
        return "historical win rate below minimum"
    if _sol._dec(metrics.get("profit_factor"), 0) < _sol._dec(cfg.get("min_profit_factor"), "1.75"):
        return "historical profit factor below minimum"
    if _sol._dec(metrics.get("drawdown_pct"), 0) > _sol._dec(cfg.get("max_leader_drawdown_pct"), 20):
        return "historical drawdown above maximum"
    if _sol._dec(metrics.get("recent_win_rate"), 0) < _sol._dec(cfg.get("min_recent_win_rate_pct"), 65):
        return "recent win rate below minimum"
    if _sol._dec(metrics.get("recent_profit_factor"), 0) < _sol._dec(cfg.get("min_recent_profit_factor"), "1.50"):
        return "recent profit factor below minimum"
    if _sol._dec(metrics.get("net"), 0) <= 0:
        return "historical net profit is not positive"

    historical_floor = max(Decimal(0), _sol._dec(cfg.get("live_min_leader_median_return_pct"), "5"))
    recent_floor = max(Decimal(0), _sol._dec(cfg.get("live_min_leader_recent_median_return_pct"), "4"))
    if _sol._dec(metrics.get("median_return_pct"), 0) < historical_floor:
        return "median return below LIVE edge floor"
    if _sol._dec(metrics.get("recent_median_return_pct"), 0) < recent_floor:
        return "recent median return below LIVE edge floor"
    return "quality gate failed"


def _selection_lookback_days(cfg: dict) -> int:
    """Use more evidence for nomination without changing recent/LIVE quality gates."""
    strategy_lookback = max(1, min(365, _sol._int(cfg.get("lookback_days"), 60)))
    requested = _sol._int(cfg.get("leader_selection_lookback_days"), max(180, strategy_lookback))
    return max(strategy_lookback, min(365, max(30, requested)))


def _broad_positive_candidates(app, cfg):
    """Return a bounded pool of profitable reconstructed wallets, not only Top-20.

    The public/research Top-20 remains produced by the original ranking function.
    Candidate discovery may use a longer bounded evidence window so wallets with a
    genuine history are not excluded merely because the short strategy lookback has
    too few closes. Quality thresholds, recent metrics, copied-performance guards and
    LIVE preflight are unchanged and are still evaluated below.
    """
    lookback = _selection_lookback_days(cfg)
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


def _evaluate_candidates(app, cfg, candidates):
    qualified = []
    failures: Counter = Counter()
    for candidate in candidates:
        wallet = str(candidate.get("wallet") or "")
        if not wallet:
            continue
        try:
            metrics = quality_metrics(app, wallet, cfg)
        except Exception as exc:
            failures[f"metrics unavailable: {type(exc).__name__}"] += 1
            continue
        if historical_ok(metrics, cfg):
            qualified.append((candidate, metrics))
        else:
            failures[_quality_failure_reason(metrics, cfg)] += 1
    return qualified, failures


def _qualified_candidates(app, cfg, candidates):
    """Compatibility helper used by tests/diagnostics."""
    return _evaluate_candidates(app, cfg, candidates)[0]


def _write_bridge(pool: int, qualified: int, selected: int, failures: Counter, cfg: dict) -> None:
    try:
        payload = {
            "schema_version": 2,
            "generated_epoch": int(time.time()),
            "pool": int(pool),
            "qualified": int(qualified),
            "selected": int(selected),
            "candidate_lookback_days": _selection_lookback_days(cfg),
            "first_failure_counts": dict(sorted((str(k), int(v)) for k, v in failures.items())),
            "thresholds": {
                "min_closed_trades": max(1, _sol._int(cfg.get("min_closed_trades"), 10)),
                "min_win_rate_pct": str(_sol._dec(cfg.get("min_win_rate_pct"), 65)),
                "min_profit_factor": str(_sol._dec(cfg.get("min_profit_factor"), "1.75")),
                "max_drawdown_pct": str(_sol._dec(cfg.get("max_leader_drawdown_pct"), 20)),
                "min_recent_win_rate_pct": str(_sol._dec(cfg.get("min_recent_win_rate_pct"), 65)),
                "min_recent_profit_factor": str(_sol._dec(cfg.get("min_recent_profit_factor"), "1.50")),
                "min_median_return_pct": str(_sol._dec(cfg.get("live_min_leader_median_return_pct"), "5")),
                "min_recent_median_return_pct": str(_sol._dec(cfg.get("live_min_leader_recent_median_return_pct"), "4")),
            },
            "thresholds_unchanged": True,
            "wallet_addresses_published": False,
        }
        with _BRIDGE_LOCK:
            _BRIDGE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _BRIDGE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(tmp, 0o644)
            os.replace(tmp, _BRIDGE)
    except Exception:
        pass


def refresh_rankings(app, telegram_id=None):
    """Keep Top-20 reporting, but search broadly for genuinely qualified leaders."""
    top = _PREV_REFRESH(app, telegram_id)
    cfg = _sol.settings(app)

    try:
        candidates = _broad_positive_candidates(app, cfg)
        qualified, failures = _evaluate_candidates(app, cfg, candidates)
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
    _write_bridge(len(candidates), len(qualified), total_selected, failures, cfg)
    print(
        "[solana-leader-edge-alignment] broader_pool=%d qualified=%d selected=%d "
        "candidate_lookback_days=%d thresholds=unchanged"
        % (len(candidates), len(qualified), total_selected, _selection_lookback_days(cfg))
    )
    return top


def install() -> None:
    if getattr(_guard, "_leader_edge_alignment_installed", False):
        return
    _guard.quality_metrics = quality_metrics
    _guard._historical_ok = historical_ok
    _sol.refresh_rankings = refresh_rankings
    _guard._leader_edge_alignment_installed = True
    print(
        "[solana-leader-edge-alignment] selector_matches_live_edge=true "
        "broader_qualified_search=true longer_candidate_evidence=true thresholds=unchanged"
    )


install()
