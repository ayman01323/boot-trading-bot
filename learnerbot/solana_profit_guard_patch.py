from __future__ import annotations

import json
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal

from . import solana_sibot as _sol

_HARD_COPIED_PROFIT_FACTOR = Decimal("1.50")
_HARD_COPIED_WIN_RATE_PCT = Decimal("50")
_HARD_MIN_COPIED_TRADES = 2

_SOL_QUALITY_DEFAULTS = {
    "require_complete_history": ("true", "Require non-truncated reconstructed Solana history for LIVE leaders"),
    "min_profit_factor": ("1.75", "Minimum historical SOL gross-profit/gross-loss ratio for leaders"),
    "recent_trade_window": ("20", "Recent Solana closed positions used for regime-change checks"),
    "min_recent_win_rate_pct": ("65", "Minimum recent Solana positive-position percentage"),
    "min_recent_profit_factor": ("1.50", "Minimum recent Solana position-level profit factor"),
    "max_leader_drawdown_pct": ("20", "Maximum reconstructed Solana leader position-level equity drawdown"),
    "leader_recent_activity_hours": ("6", "Prefer otherwise-qualified Solana leaders observed trading within this many hours"),
    "min_copied_trades_for_guard": ("2", "Minimum closed LIVE copies before copied-performance amount gate applies"),
    "min_copied_win_rate_pct": ("50", "Minimum actual Solana LIVE copied win rate"),
    "min_copied_profit_factor": ("1.50", "Minimum actual copied gross-profit/gross-loss amount ratio"),
    "max_consecutive_copied_losses": ("2", "Consecutive Solana LIVE copied losses before leader cooldown"),
    "leader_suspend_minutes": ("1440", "Solana leader cooldown after copied loss streak"),
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


def _event_key(ts, signature):
    """Match solana_sibot's deterministic same-second event ordering."""
    return (_i(ts, 0), str(signature or ""))


def _aggregate_position(rows):
    cost = sum((_d(r.get("cost_sol"), 0) for r in rows), Decimal(0))
    net = sum((_d(r.get("net_sol"), 0) for r in rows), Decimal(0))
    buy_key = min((_event_key(r.get("buy_ts"), r.get("buy_signature")) for r in rows), default=(0, ""))
    close_key = max((_event_key(r.get("sell_ts"), r.get("sell_signature")) for r in rows), default=(0, ""))
    return {
        "wallet": str(rows[0].get("wallet") or "") if rows else "",
        "mint": str(rows[0].get("mint") or "") if rows else "",
        "cost_sol": cost,
        "net_sol": net,
        "buy_key": buy_key,
        "close_key": close_key,
        "buy_ts": buy_key[0],
        "sell_ts": close_key[0],
        "fragments": len(rows),
    }


def _bucket_positions(trades):
    """Collapse matched FIFO fragments into completed wallet/mint inventory positions.

    A position remains open while a later buy overlaps any still-matched sell horizon
    for that mint. A new position starts only after the prior inventory is fully
    exited. Timestamp ties use signature ordering because solana_sibot._match_events
    uses the same deterministic `(event_ts, signature)` ordering.
    """
    grouped = defaultdict(list)
    for row in trades:
        grouped[(str(row.get("wallet") or ""), str(row.get("mint") or ""))].append(row)

    positions = []
    for rows in grouped.values():
        rows.sort(
            key=lambda r: (
                _event_key(r.get("buy_ts"), r.get("buy_signature")),
                _event_key(r.get("sell_ts"), r.get("sell_signature")),
            )
        )
        bucket = []
        bucket_close_key = None
        for row in rows:
            buy_key = _event_key(row.get("buy_ts"), row.get("buy_signature"))
            if bucket and bucket_close_key is not None and buy_key > bucket_close_key:
                positions.append(_aggregate_position(bucket))
                bucket = []
                bucket_close_key = None
            bucket.append(row)
            sell_key = _event_key(row.get("sell_ts"), row.get("sell_signature"))
            if bucket_close_key is None or sell_key > bucket_close_key:
                bucket_close_key = sell_key
        if bucket:
            positions.append(_aggregate_position(bucket))

    positions.sort(key=lambda p: (p["close_key"], p["buy_key"], p["mint"]))
    return positions


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


def _stats(values):
    profit = sum((_d(r["net_sol"]) for r in values if _d(r["net_sol"]) > 0), Decimal(0))
    loss = sum((-_d(r["net_sol"]) for r in values if _d(r["net_sol"]) < 0), Decimal(0))
    wins = sum(1 for r in values if _d(r["net_sol"]) > 0)
    losses = sum(1 for r in values if _d(r["net_sol"]) < 0)
    closed = len(values)
    return {
        "profit": profit,
        "loss": loss,
        "net": profit - loss,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "win_rate": Decimal(wins * 100) / Decimal(closed) if closed else Decimal(0),
        "profit_factor": _pf(profit, loss),
    }


def quality_metrics(app, wallet, cfg):
    lookback = max(1, min(365, _i(cfg.get("lookback_days"), 60)))
    cutoff = int(time.time()) - lookback * 86400
    recent_n = max(5, min(100, _i(cfg.get("recent_trade_window"), 20)))
    with closing(_sol.connect(app)) as conn:
        # Read the complete reconstructed wallet history first. Filtering fragments
        # by sell_ts before bucketing can cut a scaled position in half at the
        # lookback boundary and manufacture a false win/loss.
        fragments_all = [dict(r) for r in conn.execute(
            """SELECT wallet,mint,buy_signature,sell_signature,buy_ts,sell_ts,cost_sol,net_sol
               FROM trades WHERE wallet=?
               ORDER BY buy_ts,buy_signature,sell_ts,sell_signature""",
            (str(wallet),),
        ).fetchall()]
        hs = conn.execute("SELECT truncated,error FROM history_status WHERE wallet=?", (str(wallet),)).fetchone()
        candidate = conn.execute("SELECT last_seen FROM candidates WHERE wallet=?", (str(wallet),)).fetchone()

    positions_all = _bucket_positions(fragments_all)
    positions = [p for p in positions_all if int(p.get("sell_ts") or 0) >= cutoff]
    fragments = [r for r in fragments_all if int(r.get("sell_ts") or 0) >= cutoff]

    all_s = _stats(positions)
    recent = _stats(positions[-recent_n:])
    fragment_s = _stats(fragments)
    trade_last = max((int(p.get("sell_ts") or 0) for p in positions_all), default=0)
    candidate_last = int(candidate["last_seen"] or 0) if candidate else 0
    all_s.update({
        "drawdown_pct": _drawdown(positions),
        "recent_closed": recent["closed"],
        "recent_win_rate": recent["win_rate"],
        "recent_profit_factor": recent["profit_factor"],
        "history_complete": bool(hs and not int(hs["truncated"] or 0) and not str(hs["error"] or "").strip()),
        "last_activity_ts": max(trade_last, candidate_last),
        # Diagnostics make the measurement change auditable without allowing the
        # old fragment unit to drive any eligibility gate.
        "position_closed": all_s["closed"],
        "fragment_closed": fragment_s["closed"],
        "fragment_win_rate": fragment_s["win_rate"],
        "fragment_profit_factor": fragment_s["profit_factor"],
    })
    return all_s


def _historical_ok(m, cfg):
    if _sol._bool(cfg.get("require_complete_history"), True) and not m.get("history_complete"):
        return False
    # `closed` is deliberately position-level. Fragment count must never satisfy
    # the minimum evidence requirement for a leader.
    if int(m.get("closed") or 0) < max(1, _i(cfg.get("min_closed_trades"), 10)):
        return False
    if _d(m.get("win_rate")) < _d(cfg.get("min_win_rate_pct"), 65):
        return False
    if _d(m.get("profit_factor")) < _d(cfg.get("min_profit_factor"), "1.75"):
        return False
    if _d(m.get("drawdown_pct")) > _d(cfg.get("max_leader_drawdown_pct"), 20):
        return False
    if _d(m.get("recent_win_rate")) < _d(cfg.get("min_recent_win_rate_pct"), 65):
        return False
    if _d(m.get("recent_profit_factor")) < _d(cfg.get("min_recent_profit_factor"), "1.50"):
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
    profit = sum((n for n,_ in vals if n > 0), Decimal(0))
    loss = sum((-n for n,_ in vals if n < 0), Decimal(0))
    wins = sum(1 for n,_ in vals if n > 0); closed = len(vals)
    streak = 0
    for n,_ in vals:
        if n < 0: streak += 1
        else: break
    return {
        "closed": closed,
        "wins": wins,
        "losses": closed - wins,
        "gross_profit_sol": profit,
        "gross_loss_sol": loss,
        "net_sol": profit - loss,
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
        limit = max(1, _i(cfg.get("max_consecutive_copied_losses"), 2)); latest = int(m.get("latest_closed_at") or 0)
        if m["consecutive_losses"] >= limit and latest and latest != seen:
            until = now + max(1440, _i(cfg.get("leader_suspend_minutes"), 1440))*60
            _sol._set_state(conn, key, json.dumps({"until":until,"latest_closed_at":latest}))
            return False

    min_copied = max(_HARD_MIN_COPIED_TRADES, max(1, _i(cfg.get("min_copied_trades_for_guard"), 2)))
    if m["closed"] >= min_copied:
        min_win = max(_HARD_COPIED_WIN_RATE_PCT, _d(cfg.get("min_copied_win_rate_pct"), 50))
        min_pf = max(_HARD_COPIED_PROFIT_FACTOR, _d(cfg.get("min_copied_profit_factor"), "1.50"))
        if m["win_rate"] < min_win:
            return False
        # Amount is primary: copied gross profit must exceed copied gross loss by
        # the configured safety factor. This permits fewer but larger winners while
        # rejecting a strategy whose win count looks acceptable but loses more SOL.
        if m["gross_profit_sol"] <= m["gross_loss_sol"] * min_pf:
            return False
        if m["net_sol"] <= 0 or m["profit_factor"] < min_pf:
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
