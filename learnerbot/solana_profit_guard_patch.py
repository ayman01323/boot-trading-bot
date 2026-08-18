from __future__ import annotations

import json
import time
from contextlib import closing
from decimal import Decimal

from . import solana_sibot as _sol

_SOL_QUALITY_DEFAULTS = {
    "require_complete_history": ("true", "Require non-truncated reconstructed Solana history for LIVE leaders"),
    "min_profit_factor": ("1.5", "Minimum historical SOL gross-profit/gross-loss ratio for leaders"),
    "recent_trade_window": ("20", "Recent Solana closed trades used for regime-change checks"),
    "min_recent_win_rate_pct": ("55", "Minimum recent Solana positive-trade percentage"),
    "min_recent_profit_factor": ("1.10", "Minimum recent Solana profit factor"),
    "max_leader_drawdown_pct": ("20", "Maximum reconstructed Solana leader equity drawdown"),
    "leader_recent_activity_hours": ("6", "Prefer otherwise-qualified Solana leaders observed trading within this many hours"),
    "min_copied_trades_for_guard": ("3", "Minimum closed LIVE copies before copied-performance gate applies"),
    "min_copied_win_rate_pct": ("40", "Minimum actual Solana LIVE copied win rate"),
    "min_copied_profit_factor": ("1.0", "Minimum actual Solana LIVE copied profit factor"),
    "max_consecutive_copied_losses": ("3", "Consecutive Solana LIVE copied losses before leader cooldown"),
    "leader_suspend_minutes": ("360", "Solana leader cooldown after copied loss streak"),
}
_sol.DEFAULTS.update(_SOL_QUALITY_DEFAULTS)
_PREV_REFRESH = _sol.refresh_rankings


def _d(v, default="0"):
    return _sol._dec(v, default)


def _i(v, default=0):
    return _sol._int(v, default)


def _pf(profit, loss):
    p = _d(profit); l = _d(loss)
    if l > 0:
        return p / l
    return Decimal("99") if p > 0 else Decimal(0)


def _drawdown(rows):
    equity = Decimal(1); peak = Decimal(1); worst = Decimal(0)
    for r in rows:
        cost = _d(r.get("cost_sol"), 0); net = _d(r.get("net_sol"), 0)
        if cost <= 0:
            continue
        ret = max(Decimal("-0.95"), min(Decimal("5"), net / cost))
        equity *= Decimal(1) + ret
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * Decimal(100))
    return worst


def quality_metrics(app, wallet, cfg):
    lookback = max(1, min(365, _i(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    recent_n = max(5, min(100, _i(cfg.get("recent_trade_window"), 20)))
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT cost_sol,net_sol,sell_ts FROM trades WHERE wallet=? AND sell_ts>=? ORDER BY sell_ts",
            (str(wallet), cutoff),
        ).fetchall()]
        hs = conn.execute("SELECT truncated,error FROM history_status WHERE wallet=?", (str(wallet),)).fetchone()
        candidate = conn.execute("SELECT last_seen FROM candidates WHERE wallet=?", (str(wallet),)).fetchone()
    def stats(values):
        profit = sum((_d(r["net_sol"]) for r in values if _d(r["net_sol"]) > 0), Decimal(0))
        loss = sum((-_d(r["net_sol"]) for r in values if _d(r["net_sol"]) < 0), Decimal(0))
        wins = sum(1 for r in values if _d(r["net_sol"]) > 0)
        closed = len(values)
        return {
            "profit": profit, "loss": loss, "net": profit-loss, "closed": closed,
            "win_rate": Decimal(wins * 100) / Decimal(closed) if closed else Decimal(0),
            "profit_factor": _pf(profit, loss),
        }
    all_s = stats(rows); recent = stats(rows[-recent_n:])
    trade_last = max((int(r.get("sell_ts") or 0) for r in rows), default=0)
    candidate_last = int(candidate["last_seen"] or 0) if candidate else 0
    all_s.update({
        "drawdown_pct": _drawdown(rows),
        "recent_closed": recent["closed"],
        "recent_win_rate": recent["win_rate"],
        "recent_profit_factor": recent["profit_factor"],
        "history_complete": bool(hs and not int(hs["truncated"] or 0) and not str(hs["error"] or "").strip()),
        "last_activity_ts": max(trade_last, candidate_last),
    })
    return all_s


def _historical_ok(m, cfg):
    if _sol._bool(cfg.get("require_complete_history"), True) and not m.get("history_complete"):
        return False
    if int(m.get("closed") or 0) < max(1, _i(cfg.get("min_closed_trades"), 10)):
        return False
    if _d(m.get("win_rate")) < _d(cfg.get("min_win_rate_pct"), 55):
        return False
    if _d(m.get("profit_factor")) < _d(cfg.get("min_profit_factor"), "1.5"):
        return False
    if _d(m.get("drawdown_pct")) > _d(cfg.get("max_leader_drawdown_pct"), 20):
        return False
    if _d(m.get("recent_win_rate")) < _d(cfg.get("min_recent_win_rate_pct"), 55):
        return False
    if _d(m.get("recent_profit_factor")) < _d(cfg.get("min_recent_profit_factor"), "1.10"):
        return False
    return _d(m.get("net")) > 0


def _copied_metrics(app, tid, wallet):
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT realised_net_sol,closed_at FROM positions
               WHERE telegram_id=? AND leader_wallet=? AND status='CLOSED' AND mode='LIVE'
               ORDER BY closed_at DESC LIMIT 50""",
            (str(tid), str(wallet)),
        ).fetchall()]
    vals = [(_d(r.get("realised_net_sol"), 0), int(r.get("closed_at") or 0)) for r in rows]
    profit = sum((n for n,_ in vals if n > 0), Decimal(0)); loss = sum((-n for n,_ in vals if n < 0), Decimal(0))
    wins = sum(1 for n,_ in vals if n > 0); closed = len(vals)
    streak = 0
    for n,_ in vals:
        if n < 0: streak += 1
        else: break
    return {
        "closed": closed,
        "win_rate": Decimal(wins*100)/Decimal(closed) if closed else Decimal(0),
        "profit_factor": _pf(profit, loss),
        "consecutive_losses": streak,
        "latest_closed_at": vals[0][1] if vals else 0,
    }


def _copied_ok(app, tid, wallet, cfg):
    m = _copied_metrics(app, tid, wallet)
    key = f"sol_profit_guard_suspend:{tid}:{wallet}"
    now = int(time.time())
    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        raw = _sol._state(conn, key, "") or ""
        try: state = json.loads(raw) if raw else {}
        except Exception: state = {}
        until = int(state.get("until") or 0); seen = int(state.get("latest_closed_at") or 0)
        if now < until:
            return False
        limit = max(1, _i(cfg.get("max_consecutive_copied_losses"), 3)); latest = int(m.get("latest_closed_at") or 0)
        if m["consecutive_losses"] >= limit and latest and latest != seen:
            until = now + max(5, _i(cfg.get("leader_suspend_minutes"), 360))*60
            _sol._set_state(conn, key, json.dumps({"until":until,"latest_closed_at":latest}))
            return False
    min_copied = max(1, _i(cfg.get("min_copied_trades_for_guard"), 3))
    if m["closed"] >= min_copied:
        if m["win_rate"] < _d(cfg.get("min_copied_win_rate_pct"), 40):
            return False
        if m["profit_factor"] < _d(cfg.get("min_copied_profit_factor"), 1):
            return False
    return True


def refresh_rankings(app, telegram_id=None):
    # Let the original function preserve the broad positive-net Top-20 research,
    # then replace only each user's automatic leader set with stricter evidence.
    top = _PREV_REFRESH(app, telegram_id)
    cfg = _sol.settings(app)
    users = [u for u in _sol.all_users(app.csv_dir, enabled_only=True) if str(u.get("status") or "").upper()=="ACTIVE"]
    if telegram_id is not None:
        users = [u for u in users if str(u.get("telegram_id")) == str(telegram_id)]
    qualified = []
    for a in top:
        wallet = str(a.get("wallet") or "")
        m = quality_metrics(app, wallet, cfg)
        if _historical_ok(m, cfg):
            qualified.append((a, m))

    # Profitability remains mandatory. Within that qualified pool, prefer wallets
    # observed trading recently so leader slots are less likely to be occupied by
    # excellent but currently inactive wallets. If there are not enough recently
    # active wallets, older qualified leaders still fill the remaining slots.
    now = int(time.time())
    activity_hours = max(1, min(168, _i(cfg.get("leader_recent_activity_hours"), 6)))
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
    leaders_n = max(1, min(10, _i(cfg.get("leaders_per_user"), 3)))

    selected = {}
    for u in users:
        tid = str(u.get("telegram_id") or "")
        choices = []
        for a,m in qualified:
            wallet = str(a.get("wallet") or "")
            if _copied_ok(app, tid, wallet, cfg):
                choices.append((wallet, m))
            if len(choices) >= leaders_n:
                break
        selected[tid] = choices

    with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
        for u in users:
            tid = str(u.get("telegram_id") or "")
            old = {str(r["wallet"]): int(r["selected_at"] or now) for r in conn.execute("SELECT wallet,selected_at FROM leaders WHERE telegram_id=?", (tid,)).fetchall()}
            conn.execute("DELETE FROM leaders WHERE telegram_id=?", (tid,))
            for rank,(wallet,m) in enumerate(selected.get(tid, []), 1):
                conn.execute(
                    "INSERT INTO leaders(telegram_id,rank,wallet,net_profit_sol,win_rate,closed_trades,selected_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (tid, rank, wallet, str(m["net"]), float(m["win_rate"]), int(m["closed"]), old.get(wallet, now), now),
                )
        conn.commit()
    _sol.export_csv(app)
    return top


def install():
    if getattr(_sol, "_profit_guard_patch_installed", False):
        return
    _sol.refresh_rankings = refresh_rankings
    _sol._profit_guard_patch_installed = True


install()
