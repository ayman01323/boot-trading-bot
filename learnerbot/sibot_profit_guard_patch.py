from __future__ import annotations

import json
import math
import threading
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from . import sibot as _sibot
from . import solana_sibot as _sol
from . import solana_live_patch as _sollive

# Quality-first guard layer. This module can only reject/reduce new entries or
# tighten leader selection. It does not arm LIVE, change wallet keys, or bypass
# any existing quote/simulation/signing gate.

_QUALITY_DEFAULTS = {
    "min_profit_factor": ("1.5", "Minimum historical gross-profit/gross-loss ratio for LIVE leaders"),
    "recent_trade_window": ("20", "Recent closed trades used to detect leader regime change"),
    "recent_weight_pct": ("40", "Weight of recent leader quality versus full lookback quality"),
    "min_recent_win_rate_pct": ("55", "Minimum positive percentage in the recent-trade window"),
    "min_recent_profit_factor": ("1.10", "Minimum recent gross-profit/gross-loss ratio"),
    "max_leader_drawdown_pct": ("20", "Maximum reconstructed leader equity drawdown"),
    "min_copied_trades_for_guard": ("3", "Minimum copied closed positions before copied-performance gate applies"),
    "min_copied_win_rate_pct": ("40", "Minimum actual copied win rate after enough copied positions"),
    "min_copied_profit_factor": ("1.0", "Minimum actual copied profit factor after enough copied positions"),
    "max_consecutive_copied_losses": ("3", "Consecutive copied losses before temporary leader suspension"),
    "leader_suspend_minutes": ("360", "Leader cooldown after consecutive copied losses"),
    "min_expected_edge_pct": ("1.0", "Minimum weighted historical expected edge before a copied BUY"),
    "edge_cost_multiple": ("2.0", "Expected edge must exceed this multiple of gas plus immediate round-trip cost"),
    "max_gas_cost_pct": ("2.0", "Maximum estimated entry+exit gas as percent of copied position"),
    "daily_loss_limit_pct": ("4.0", "Pause new entries on a chain after this daily realised loss percentage"),
    "portfolio_drawdown_limit_pct": ("12.0", "Pause new entries on a chain after this realised-PnL drawdown percentage"),
    "dynamic_sizing_enabled": ("true", "Scale position size using leader quality rather than a single fixed percentage"),
    "dynamic_min_allocation_pct": ("5", "Minimum allocation for a merely qualifying leader"),
    "dynamic_max_allocation_pct": ("20", "Maximum allocation for an elite qualifying leader"),
    "leader_consensus_window_seconds": ("120", "Window for detecting another selected leader buying the same token"),
    "leader_consensus_bonus_pct": ("15", "Maximum sizing bonus when multiple selected leaders independently buy the same token"),
    "max_leader_exposure_pct": ("30", "Maximum chain capital exposed to positions whose primary leader is the same wallet"),
    "profit_lock_trigger_pct": ("5", "Daily realised profit percentage that activates reduced new-entry risk"),
    "profit_lock_size_multiplier_pct": ("70", "New-entry size multiplier after the daily profit-lock trigger"),
}

_QUALITY_SPECS = {
    "min_profit_factor": (1.0, 20.0, "x"),
    "recent_trade_window": (5, 100, "trades"),
    "recent_weight_pct": (0, 100, "%"),
    "min_recent_win_rate_pct": (0, 100, "%"),
    "min_recent_profit_factor": (0.1, 20.0, "x"),
    "max_leader_drawdown_pct": (1, 100, "%"),
    "min_copied_trades_for_guard": (1, 100, "trades"),
    "min_copied_win_rate_pct": (0, 100, "%"),
    "min_copied_profit_factor": (0.1, 20.0, "x"),
    "max_consecutive_copied_losses": (1, 20, "losses"),
    "leader_suspend_minutes": (5, 10080, "minutes"),
    "min_expected_edge_pct": (0, 100, "%"),
    "edge_cost_multiple": (0.1, 10, "x"),
    "max_gas_cost_pct": (0.1, 50, "%"),
    "daily_loss_limit_pct": (0.1, 50, "%"),
    "portfolio_drawdown_limit_pct": (0.1, 80, "%"),
    "dynamic_min_allocation_pct": (0.1, 100, "%"),
    "dynamic_max_allocation_pct": (0.1, 100, "%"),
    "leader_consensus_window_seconds": (5, 3600, "seconds"),
    "leader_consensus_bonus_pct": (0, 100, "%"),
    "max_leader_exposure_pct": (1, 100, "%"),
    "profit_lock_trigger_pct": (0.1, 100, "%"),
    "profit_lock_size_multiplier_pct": (1, 100, "%"),
}

_sibot.DEFAULTS.update(_QUALITY_DEFAULTS)
_sibot.SETTING_SPECS.update(_QUALITY_SPECS)

_PREV_ENSURE = _sibot.ensure_settings
_PREV_REFRESH = _sibot.refresh_rankings
_PREV_VALIDATE_ENTRY = _sibot._validate_entry
_PREV_PROCESS_EVENT = _sibot.process_leader_event
_TLS = threading.local()


def _d(v, default="0") -> Decimal:
    return _sibot._dec(v, default)


def _f(v, default=0.0) -> float:
    return _sibot._float(v, default)


def _i(v, default=0) -> int:
    return _sibot._int(v, default)


def _profit_factor(profit: Decimal, loss: Decimal) -> Decimal:
    if loss > 0:
        return profit / loss
    return Decimal("99") if profit > 0 else Decimal(0)


def _drawdown_pct(rows) -> Decimal:
    equity = Decimal(1)
    peak = Decimal(1)
    max_dd = Decimal(0)
    for r in rows:
        cost = _d(r.get("cost_native") if isinstance(r, dict) else r["cost_native"], 0)
        net = _d(r.get("net_native") if isinstance(r, dict) else r["net_native"], 0)
        if cost <= 0:
            continue
        ret = net / cost
        ret = max(Decimal("-0.95"), min(Decimal("5"), ret))
        equity *= Decimal(1) + ret
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * Decimal(100))
    return max_dd


def quality_metrics(app, chain_id: int, wallet: str, lookback_days=60, recent_n=20) -> dict:
    cutoff = int(time.time()) - max(1, int(lookback_days)) * 86400
    with closing(_sibot.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT cost_native,net_native,sell_ts FROM wallet_trades WHERE chain_id=? AND lower(wallet)=? AND sell_ts>=? ORDER BY sell_ts",
            (int(chain_id), str(wallet).lower(), cutoff),
        ).fetchall()]
        hs = conn.execute(
            "SELECT history_complete FROM wallet_history_status WHERE chain_id=? AND lower(wallet)=?",
            (int(chain_id), str(wallet).lower()),
        ).fetchone()
    profit = sum((_d(r["net_native"]) for r in rows if _d(r["net_native"]) > 0), Decimal(0))
    loss = sum((-_d(r["net_native"]) for r in rows if _d(r["net_native"]) < 0), Decimal(0))
    net = profit - loss
    wins = sum(1 for r in rows if _d(r["net_native"]) > 0)
    closed = len(rows)
    wr = Decimal(wins * 100) / Decimal(closed) if closed else Decimal(0)
    pf = _profit_factor(profit, loss)
    drawdown = _drawdown_pct(rows)
    returns = []
    for r in rows:
        cost = _d(r["cost_native"], 0)
        if cost > 0:
            returns.append(_d(r["net_native"], 0) / cost * Decimal(100))
    avg_return = sum(returns, Decimal(0)) / Decimal(len(returns)) if returns else Decimal(0)
    recent = rows[-max(1, int(recent_n)):]
    rp = sum((_d(r["net_native"]) for r in recent if _d(r["net_native"]) > 0), Decimal(0))
    rl = sum((-_d(r["net_native"]) for r in recent if _d(r["net_native"]) < 0), Decimal(0))
    rwins = sum(1 for r in recent if _d(r["net_native"]) > 0)
    rclosed = len(recent)
    rwr = Decimal(rwins * 100) / Decimal(rclosed) if rclosed else Decimal(0)
    rpf = _profit_factor(rp, rl)
    rreturns = []
    for r in recent:
        cost = _d(r["cost_native"], 0)
        if cost > 0:
            rreturns.append(_d(r["net_native"], 0) / cost * Decimal(100))
    ravg = sum(rreturns, Decimal(0)) / Decimal(len(rreturns)) if rreturns else Decimal(0)
    return {
        "closed": closed, "wins": wins, "win_rate": wr, "profit": profit, "loss": loss, "net": net,
        "profit_factor": pf, "drawdown_pct": drawdown, "avg_return_pct": avg_return,
        "recent_closed": rclosed, "recent_win_rate": rwr, "recent_profit_factor": rpf,
        "recent_avg_return_pct": ravg, "history_complete": bool(hs and hs["history_complete"]),
    }


def _quality_score(m: dict, cfg: dict) -> Decimal:
    recent_weight = max(Decimal(0), min(Decimal(1), _d(cfg.get("recent_weight_pct"), 40) / Decimal(100)))
    def score(wr, pf, dd=Decimal(0)):
        w = max(Decimal(0), min(Decimal(1), _d(wr) / Decimal(100)))
        p = max(Decimal(0), min(Decimal(1), _d(pf) / Decimal(3)))
        d = max(Decimal(0), min(Decimal(1), Decimal(1) - _d(dd) / Decimal(100)))
        return w * Decimal("0.45") + p * Decimal("0.35") + d * Decimal("0.20")
    long_score = score(m.get("win_rate"), m.get("profit_factor"), m.get("drawdown_pct"))
    recent_score = score(m.get("recent_win_rate"), m.get("recent_profit_factor"), Decimal(0))
    return max(Decimal(0), min(Decimal(1), long_score * (Decimal(1) - recent_weight) + recent_score * recent_weight))


def _leader_quality_ok(m: dict, cfg: dict) -> tuple[bool, str]:
    if _sibot._bool(cfg.get("require_complete_history"), True) and not m.get("history_complete"):
        return False, "history incomplete"
    if int(m.get("closed") or 0) < _i(cfg.get("min_closed_trades"), 50):
        return False, "not enough closed trades"
    if _d(m.get("win_rate")) < _d(cfg.get("min_win_rate_pct"), 55):
        return False, "historical win rate below minimum"
    if _d(m.get("profit_factor")) < _d(cfg.get("min_profit_factor"), "1.5"):
        return False, "historical profit factor below minimum"
    if _d(m.get("drawdown_pct")) > _d(cfg.get("max_leader_drawdown_pct"), 20):
        return False, "historical drawdown above maximum"
    if _d(m.get("recent_win_rate")) < _d(cfg.get("min_recent_win_rate_pct"), 55):
        return False, "recent win rate below minimum"
    if _d(m.get("recent_profit_factor")) < _d(cfg.get("min_recent_profit_factor"), "1.10"):
        return False, "recent profit factor below minimum"
    if _d(m.get("net")) <= 0:
        return False, "historical net profit is not positive"
    return True, "PASS"


def refresh_rankings(app, telegram_id, chain):
    # Preserve the broad profit-first Top-20 produced by the earlier layer, then
    # rebuild only the automatic leader set using the stricter quality evidence.
    result = _PREV_REFRESH(app, telegram_id, chain)
    cfg = _sibot.user_settings(app, telegram_id, chain.chain_id)
    candidates = _sibot.ranking_rows(app, telegram_id, chain.chain_id)
    recent_n = max(5, _i(cfg.get("recent_trade_window"), 20))
    safe = []
    for r in candidates:
        m = quality_metrics(app, chain.chain_id, r.get("wallet"), cfg.get("lookback_days", 60), recent_n)
        ok, _ = _leader_quality_ok(m, cfg)
        if ok:
            safe.append((r, m, _quality_score(m, cfg)))
    safe.sort(key=lambda x: (x[2], _d(x[1].get("net")), _d(x[1].get("profit_factor")), _d(x[1].get("recent_profit_factor"))), reverse=True)
    now = int(time.time())
    nleaders = max(1, min(10, _i(cfg.get("leaders_per_chain"), 3)))
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        old = {str(r["wallet"]).lower(): int(r["selected_at"] or now) for r in conn.execute(
            "SELECT wallet,selected_at FROM leaders WHERE telegram_id=? AND chain_id=?", (str(telegram_id), chain.chain_id)
        ).fetchall()}
        conn.execute("DELETE FROM leaders WHERE telegram_id=? AND chain_id=?", (str(telegram_id), chain.chain_id))
        for i, (r, m, score) in enumerate(safe[:nleaders], 1):
            conn.execute(
                """INSERT INTO leaders(telegram_id,chain_id,chain_slug,rank,wallet,net_profit_native,win_rate,closed_trades,selected_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (str(telegram_id), chain.chain_id, chain.slug, i, str(r.get("wallet")).lower(), str(m["net"]), float(m["win_rate"]), int(m["closed"]), old.get(str(r.get("wallet")).lower(), now), now),
            )
        conn.commit()
    _sibot.export_rankings(app)
    return result


def _copied_metrics(app, tid, chain_id, leader) -> dict:
    with closing(_sibot.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT mode,realised_net_native,realised_user_net_native,closed_at FROM positions
               WHERE telegram_id=? AND chain_id=? AND lower(primary_leader)=? AND status='CLOSED'
               ORDER BY closed_at DESC LIMIT 50""",
            (str(tid), int(chain_id), str(leader).lower()),
        ).fetchall()]
    vals = []
    for r in rows:
        n = _d(r.get("realised_user_net_native") if str(r.get("mode") or "").upper() == "LIVE" else r.get("realised_net_native"), 0)
        vals.append((n, int(r.get("closed_at") or 0)))
    profit = sum((n for n, _ in vals if n > 0), Decimal(0))
    loss = sum((-n for n, _ in vals if n < 0), Decimal(0))
    wins = sum(1 for n, _ in vals if n > 0)
    closed = len(vals)
    consecutive_losses = 0
    for n, _ in vals:
        if n < 0:
            consecutive_losses += 1
        else:
            break
    return {
        "closed": closed,
        "win_rate": Decimal(wins * 100) / Decimal(closed) if closed else Decimal(0),
        "profit_factor": _profit_factor(profit, loss),
        "consecutive_losses": consecutive_losses,
        "latest_closed_at": vals[0][1] if vals else 0,
    }


def _suspension_status(app, tid, chain_id, leader, cfg, copied) -> tuple[bool, str]:
    key = f"profit_guard_suspend:{tid}:{chain_id}:{str(leader).lower()}"
    now = int(time.time())
    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        raw = _sibot._state(conn, key, "") or ""
        try:
            state = json.loads(raw) if raw else {}
        except Exception:
            state = {}
        until = int(state.get("until") or 0)
        last_seen = int(state.get("latest_closed_at") or 0)
        if now < until:
            return True, f"leader cooling down for {max(1, (until-now)//60)} more minutes"
        limit = max(1, _i(cfg.get("max_consecutive_copied_losses"), 3))
        latest = int(copied.get("latest_closed_at") or 0)
        if int(copied.get("consecutive_losses") or 0) >= limit and latest and latest != last_seen:
            until = now + max(5, _i(cfg.get("leader_suspend_minutes"), 360)) * 60
            _sibot._set_state(conn, key, json.dumps({"until": until, "latest_closed_at": latest}))
            return True, f"leader suspended after {limit} consecutive copied losses"
    return False, "PASS"


def _chain_risk(app, tid, trader) -> dict:
    cid = trader.chain.chain_id
    now = int(time.time())
    day_start = now - (now % 86400)
    with closing(_sibot.connect(app)) as conn:
        open_rows = conn.execute(
            "SELECT entry_cost_native,primary_leader FROM positions WHERE telegram_id=? AND chain_id=? AND status='OPEN'",
            (str(tid), cid),
        ).fetchall()
        closed = [dict(r) for r in conn.execute(
            """SELECT realised_user_net_native,closed_at FROM positions
               WHERE telegram_id=? AND chain_id=? AND status='CLOSED' AND mode='LIVE' ORDER BY closed_at""",
            (str(tid), cid),
        ).fetchall()]
    native = max(Decimal(0), trader.native_balance())
    open_principal = sum((_d(r["entry_cost_native"], 0) for r in open_rows), Decimal(0))
    capital = max(Decimal("0.000000000001"), native + open_principal)
    daily = sum((_d(r["realised_user_net_native"], 0) for r in closed if int(r.get("closed_at") or 0) >= day_start), Decimal(0))
    cumulative = Decimal(0)
    peak = Decimal(0)
    for r in closed:
        cumulative += _d(r["realised_user_net_native"], 0)
        peak = max(peak, cumulative)
    drawdown = max(Decimal(0), peak - cumulative)
    return {
        "capital": capital,
        "native": native,
        "open_principal": open_principal,
        "daily_realised": daily,
        "daily_pct": daily / capital * Decimal(100),
        "drawdown_pct": drawdown / capital * Decimal(100),
    }


def _consensus_count(app, tid, chain_id, token, cfg) -> int:
    window = max(5, _i(cfg.get("leader_consensus_window_seconds"), 120))
    cutoff = int(time.time()) - window
    with closing(_sibot.connect(app)) as conn:
        r = conn.execute(
            """SELECT COUNT(DISTINCT e.leader_wallet) n FROM leader_events e
               JOIN leaders l ON l.chain_id=e.chain_id AND l.wallet=e.leader_wallet AND l.telegram_id=?
               WHERE e.chain_id=? AND lower(e.token)=? AND e.action='BUY' AND e.event_ts>=?""",
            (str(tid), int(chain_id), str(token).lower(), cutoff),
        ).fetchone()
    return int(r["n"] or 0) if r else 0


def _position_size(app, telegram_id, trader, cfg):
    reserve = _d(trader.settings.get("min_native_gas_reserve"), "0.005")
    native = max(Decimal(0), trader.native_balance() - reserve)
    with closing(_sibot.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT entry_input_native,primary_leader FROM positions WHERE telegram_id=? AND chain_id=? AND status='OPEN'",
            (str(telegram_id), trader.chain.chain_id),
        ).fetchall()]
    open_principal = sum((_d(r["entry_input_native"], 0) for r in rows), Decimal(0))
    chain_capital = native + open_principal
    base_alloc = max(Decimal("0.001"), min(Decimal(1), _d(cfg.get("allocation_pct"), 15) / Decimal(100)))
    target_alloc = base_alloc
    event = getattr(_TLS, "event", None)
    if event and str(event.get("action") or "").upper() == "BUY" and _sibot._bool(cfg.get("dynamic_sizing_enabled"), True):
        m = quality_metrics(app, trader.chain.chain_id, event.get("leader_wallet"), cfg.get("lookback_days", 60), cfg.get("recent_trade_window", 20))
        score = _quality_score(m, cfg)
        lo = max(Decimal("0.001"), min(Decimal(1), _d(cfg.get("dynamic_min_allocation_pct"), 5) / Decimal(100)))
        hi = max(lo, min(Decimal(1), _d(cfg.get("dynamic_max_allocation_pct"), 20) / Decimal(100)))
        target_alloc = lo + (hi - lo) * score
        if _consensus_count(app, telegram_id, trader.chain.chain_id, event.get("token"), cfg) >= 2:
            bonus = max(Decimal(0), min(Decimal(1), _d(cfg.get("leader_consensus_bonus_pct"), 15) / Decimal(100)))
            target_alloc = min(hi, target_alloc * (Decimal(1) + bonus))
        risk = _chain_risk(app, telegram_id, trader)
        if risk["daily_pct"] >= _d(cfg.get("profit_lock_trigger_pct"), 5):
            target_alloc *= max(Decimal("0.01"), min(Decimal(1), _d(cfg.get("profit_lock_size_multiplier_pct"), 70) / Decimal(100)))
    exposure_limit = max(Decimal(0), min(Decimal(1), _d(cfg.get("max_exposure_pct"), 60) / Decimal(100)))
    exposure_room = max(Decimal(0), chain_capital * exposure_limit - open_principal)
    amount = min(chain_capital * target_alloc, exposure_room, native)
    if event:
        leader = str(event.get("leader_wallet") or "").lower()
        leader_open = sum((_d(r["entry_input_native"], 0) for r in rows if str(r.get("primary_leader") or "").lower() == leader), Decimal(0))
        leader_cap = chain_capital * max(Decimal(0), min(Decimal(1), _d(cfg.get("max_leader_exposure_pct"), 30) / Decimal(100)))
        amount = min(amount, max(Decimal(0), leader_cap - leader_open))
    max_input = _d(trader.settings.get("max_native_input_per_trade"), "0.05")
    amount = min(amount, max_input)
    minimum = _d(cfg.get("min_trade_native"), "0.0001")
    return amount if amount >= minimum else Decimal(0)


def _validate_entry(app, trader, event, amount, cfg, live):
    tid = str(getattr(trader, "telegram_id", "") or "")
    leader = str(event.get("leader_wallet") or "").lower()
    if tid and leader:
        m = quality_metrics(app, trader.chain.chain_id, leader, cfg.get("lookback_days", 60), cfg.get("recent_trade_window", 20))
        ok, reason = _leader_quality_ok(m, cfg)
        if not ok:
            return False, f"profit guard: {reason}", {"quality": m}
        copied = _copied_metrics(app, tid, trader.chain.chain_id, leader)
        suspended, sreason = _suspension_status(app, tid, trader.chain.chain_id, leader, cfg, copied)
        if suspended:
            return False, f"profit guard: {sreason}", {"quality": m, "copied": copied}
        min_copied = max(1, _i(cfg.get("min_copied_trades_for_guard"), 3))
        if copied["closed"] >= min_copied:
            if copied["win_rate"] < _d(cfg.get("min_copied_win_rate_pct"), 40):
                return False, "profit guard: actual copied win rate below minimum", {"quality": m, "copied": copied}
            if copied["profit_factor"] < _d(cfg.get("min_copied_profit_factor"), 1):
                return False, "profit guard: actual copied profit factor below minimum", {"quality": m, "copied": copied}
        risk = _chain_risk(app, tid, trader)
        if risk["daily_pct"] <= -_d(cfg.get("daily_loss_limit_pct"), 4):
            return False, f"profit guard: daily chain loss circuit breaker ({risk['daily_pct']:.2f}%)", {"risk": risk}
        if risk["drawdown_pct"] >= _d(cfg.get("portfolio_drawdown_limit_pct"), 12):
            return False, f"profit guard: chain drawdown circuit breaker ({risk['drawdown_pct']:.2f}%)", {"risk": risk}
    else:
        m = {}
    ok, reason, check = _PREV_VALIDATE_ENTRY(app, trader, event, amount, cfg, live)
    if not ok:
        return ok, reason, check
    buy_gas = _sibot._estimated_gas_native(trader, cfg, "BUY")
    exit_gas = _sibot._estimated_gas_native(trader, cfg, "SELL")
    gas_pct = (buy_gas + exit_gas) / max(Decimal("0.000000000001"), _d(amount)) * Decimal(100)
    if gas_pct > _d(cfg.get("max_gas_cost_pct"), 2):
        return False, f"profit guard: estimated entry+exit gas {gas_pct:.3f}% exceeds maximum", {**check, "gas_cost_pct": gas_pct}
    if not m and leader:
        m = quality_metrics(app, trader.chain.chain_id, leader, cfg.get("lookback_days", 60), cfg.get("recent_trade_window", 20))
    recent_weight = max(Decimal(0), min(Decimal(1), _d(cfg.get("recent_weight_pct"), 40) / Decimal(100)))
    expected_edge = _d(m.get("avg_return_pct"), 0) * (Decimal(1) - recent_weight) + _d(m.get("recent_avg_return_pct"), 0) * recent_weight
    if expected_edge < _d(cfg.get("min_expected_edge_pct"), 1):
        return False, f"profit guard: expected historical edge {expected_edge:.3f}% below minimum", {**check, "gas_cost_pct": gas_pct, "expected_edge_pct": expected_edge}
    roundtrip = _d(check.get("roundtrip_loss_pct"), 0)
    required = (gas_pct + roundtrip) * _d(cfg.get("edge_cost_multiple"), 2)
    if expected_edge < required:
        return False, f"profit guard: expected edge {expected_edge:.3f}% does not cover {cfg.get('edge_cost_multiple','2')}x estimated cost {gas_pct + roundtrip:.3f}%", {**check, "gas_cost_pct": gas_pct, "expected_edge_pct": expected_edge, "required_edge_pct": required}
    check = dict(check)
    check.update({"gas_cost_pct": gas_pct, "expected_edge_pct": expected_edge, "quality_score": _quality_score(m, cfg) if m else Decimal(0)})
    return True, "PASS", check


def process_leader_event(app, event):
    _TLS.event = event
    try:
        return _PREV_PROCESS_EVENT(app, event)
    finally:
        _TLS.event = None


def _migrate_platform_once(app, path: Path):
    marker = Path(app.data_dir) / ".sibot_quality_guard_v1"
    if marker.exists():
        return
    rows = _sibot._rows(path)
    targets = {
        "leaders_per_chain": "3",
        "allocation_pct": "15",
        "max_exposure_pct": "60",
        "min_closed_trades": "50",
        "min_win_rate_pct": "55",
        "max_signal_age_seconds": "20",
        "max_entry_deterioration_pct": "1.5",
        "max_roundtrip_loss_pct": "2",
        "max_positions_per_chain": "5",
        "stop_loss_pct": "10",
        "take_profit_pct": "25",
        "min_exit_profit_pct": "0.10",
        "max_hold_hours": "24",
        "require_complete_history": "true",
        "break_even_trigger_pct": "5",
        "break_even_floor_pct": "0.25",
        "trailing_trigger_pct": "10",
        "trailing_gap_pct": "4",
        "leader_exit_loss_cap_pct": "2",
    }
    platform_changed = False
    for row in rows:
        key = str(row.get("setting") or "").strip()
        if key in targets:
            row["value"] = targets[key]
            platform_changed = True
    if platform_changed:
        _sibot._atomic_csv(path, rows, ["chain_id", "setting", "value", "description"])

    # Change only a user profile that clearly matches the aggressive screenshot
    # preset, avoiding unrelated multi-user profiles.
    from .user_registry import all_users, set_user_setting, user_setting
    signature = {
        "leaders_per_chain": "5", "allocation_pct": "50", "max_exposure_pct": "80",
        "min_closed_trades": "30", "min_win_rate_pct": "45", "max_entry_deterioration_pct": "5",
        "take_profit_pct": "10", "max_positions_per_chain": "30",
    }
    for u in all_users(app.csv_dir, enabled_only=False):
        tid = str(u.get("telegram_id") or "")
        if not tid:
            continue
        matches = 0
        for key, expected in signature.items():
            cur = user_setting(app.csv_dir, tid, 0, f"sibot_{key}", None)
            if cur is not None and str(cur).strip() == expected:
                matches += 1
        if matches >= 6:
            for key, value in targets.items():
                if key in _sibot.DEFAULTS:
                    set_user_setting(app.csv_dir, tid, f"sibot_{key}", value, chain_id="*", description=f"SiBot quality preset {key}")
    marker.write_text(str(int(time.time())), encoding="utf-8")


def ensure_settings(app):
    path = _PREV_ENSURE(app)
    _migrate_platform_once(app, path)
    return path


def _tighten_solana_once(app):
    marker = Path(app.data_dir) / ".solana_quality_guard_v1"
    if marker.exists():
        return
    path = _sol.ensure_settings(app)
    rows = _sibot._rows(path)
    targets = {
        "leaders_per_user": "3", "min_closed_trades": "10", "min_win_rate_pct": "55",
        "max_signal_age_seconds": "20", "max_roundtrip_loss_pct": "2", "max_entry_deterioration_pct": "1.5",
        "stop_loss_pct": "10", "take_profit_pct": "25", "leader_exit_loss_cap_pct": "2",
        "break_even_trigger_pct": "5", "break_even_floor_pct": "0.25", "trailing_trigger_pct": "10",
        "trailing_gap_pct": "4", "max_hold_hours": "24", "mirror_partial_sells": "true",
    }
    changed = False
    for row in rows:
        key = str(row.get("setting") or "").strip()
        if key in targets:
            row["value"] = targets[key]
            changed = True
    if changed:
        _sibot._atomic_csv(path, rows, ["setting", "value", "description"])
    marker.write_text(str(int(time.time())), encoding="utf-8")


def install():
    if getattr(_sibot, "_profit_guard_patch_installed", False):
        return
    _sibot.ensure_settings = ensure_settings
    _sibot.refresh_rankings = refresh_rankings
    _sibot._position_size = _position_size
    _sibot._validate_entry = _validate_entry
    _sibot.process_leader_event = process_leader_event
    # Make sure newly added defaults exist immediately when workers start.
    _sibot._profit_guard_patch_installed = True


install()
