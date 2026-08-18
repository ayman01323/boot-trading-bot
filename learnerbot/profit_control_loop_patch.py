from __future__ import annotations

import csv
import json
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import solana_profit_guard_patch as _guard
from . import solana_sibot as _sol
from . import transaction_audit_worker_patch as _worker

# Closed-loop objective requested by the owner: retain leaders/entry policies that
# produce more profitable closed LIVE copies than losing copies, with positive net
# realised P&L and profit factor > 1.10. The loop evaluates every hour but does not
# change capital, LIVE arming, signing, reserve, simulation, exit circuit breakers,
# or position-cap safety controls.
MIN_LEADER_TRADES = 3
MIN_PROFILE_TRADES = 5
MIN_PROFILE_HOURS = 2
LEADER_RETRY_COOLDOWN_SECONDS = 3 * 60 * 60
MIN_SUCCESS_PROFIT_FACTOR = Decimal("1.10")

# Only these selection/entry-quality keys may be changed by the automatic policy
# overlay. These profiles are bounded and reviewed in source control; GPT cannot
# invent arbitrary live parameter values.
CONTROLLED_KEYS = {
    "min_win_rate_pct",
    "min_profit_factor",
    "min_recent_win_rate_pct",
    "min_recent_profit_factor",
    "max_signal_age_seconds",
    "max_roundtrip_loss_pct",
    "max_entry_deterioration_pct",
}
FORBIDDEN_KEYS = {
    "live_trade_sol", "live_min_sol_reserve", "live_max_positions",
    "live_require_simulation", "live_require_execute_output",
    "live_require_swap_events", "live_no_output_disable_after",
    "solana_live_enabled", "private_key", "signing", "reserve",
}

PROFILES = {
    # Baseline means use the operator's current CSV values unchanged.
    "BASELINE": {},
    # Moderate tightening when the baseline produces more losing copies than wins.
    "PROFIT_FIRST": {
        "min_win_rate_pct": "55",
        "min_profit_factor": "1.30",
        "min_recent_win_rate_pct": "55",
        "min_recent_profit_factor": "1.10",
        "max_signal_age_seconds": "25",
        "max_roundtrip_loss_pct": "2.5",
        "max_entry_deterioration_pct": "1.5",
    },
    # Stronger edge requirement if PROFIT_FIRST is also net-negative.
    "STRICT_EDGE": {
        "min_win_rate_pct": "60",
        "min_profit_factor": "1.50",
        "min_recent_win_rate_pct": "60",
        "min_recent_profit_factor": "1.20",
        "max_signal_age_seconds": "20",
        "max_roundtrip_loss_pct": "2.0",
        "max_entry_deterioration_pct": "1.0",
    },
}
PROFILE_ORDER = ["BASELINE", "PROFIT_FIRST", "STRICT_EDGE"]

for _name, _overlay in PROFILES.items():
    if not set(_overlay).issubset(CONTROLLED_KEYS):
        raise RuntimeError(f"profit control profile {_name} contains non-controlled keys")
    if set(_overlay) & FORBIDDEN_KEYS:
        raise RuntimeError(f"profit control profile {_name} touches forbidden LIVE safety keys")

_PREV_SETTINGS = _sol.settings
_PREV_COPIED_OK = _guard._copied_ok
_ORIGINAL_GPT_REVIEW = _worker.run_hourly_gpt_review

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS control_state(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control_runs(
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  generated_at INTEGER NOT NULL,
  profile TEXT NOT NULL,
  closed_trades INTEGER NOT NULL,
  wins INTEGER NOT NULL,
  losses INTEGER NOT NULL,
  gross_profit_sol TEXT NOT NULL,
  gross_loss_sol TEXT NOT NULL,
  net_sol TEXT NOT NULL,
  profit_factor TEXT NOT NULL,
  profile_changed INTEGER NOT NULL DEFAULT 0,
  previous_profile TEXT,
  gpt_status TEXT,
  details_json TEXT
);
CREATE TABLE IF NOT EXISTS leader_registry(
  telegram_id TEXT NOT NULL,
  leader_wallet TEXT NOT NULL,
  closed_trades INTEGER NOT NULL,
  wins INTEGER NOT NULL,
  losses INTEGER NOT NULL,
  gross_profit_sol TEXT NOT NULL,
  gross_loss_sol TEXT NOT NULL,
  net_sol TEXT NOT NULL,
  profit_factor TEXT NOT NULL,
  successful INTEGER NOT NULL,
  blocked_until INTEGER NOT NULL,
  last_closed_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(telegram_id,leader_wallet)
);
CREATE TABLE IF NOT EXISTS strategy_registry(
  profile TEXT PRIMARY KEY,
  hours_observed INTEGER NOT NULL,
  closed_trades INTEGER NOT NULL,
  wins INTEGER NOT NULL,
  losses INTEGER NOT NULL,
  gross_profit_sol TEXT NOT NULL,
  gross_loss_sol TEXT NOT NULL,
  net_sol TEXT NOT NULL,
  profit_factor TEXT NOT NULL,
  successful INTEGER NOT NULL,
  last_used_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
"""


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _pf(profit: Decimal, loss: Decimal) -> Decimal:
    if loss > 0:
        return profit / loss
    return Decimal("99") if profit > 0 else Decimal(0)


def _is_success(wins: int, losses: int, net: Decimal, profit_factor: Decimal, *, min_trades: int, closed: int) -> bool:
    return (
        int(closed) >= int(min_trades)
        and int(wins) > int(losses)
        and Decimal(net) > 0
        and Decimal(profit_factor) > MIN_SUCCESS_PROFIT_FACTOR
    )


def _db_path(app) -> Path:
    return Path(app.data_dir) / "profit_control_loop.sqlite3"


def _connect(app):
    path = _db_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _get_state(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM control_state WHERE key=?", (str(key),)).fetchone()
    return str(row["value"]) if row else str(default)


def _set_state(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO control_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(key), str(value)),
    )


def active_profile(app) -> str:
    try:
        with closing(_connect(app)) as conn:
            value = _get_state(conn, "active_profile", "BASELINE")
    except Exception:
        return "BASELINE"
    return value if value in PROFILES else "BASELINE"


def settings_with_profit_control(app) -> dict:
    cfg = dict(_PREV_SETTINGS(app))
    profile = active_profile(app)
    cfg.update(PROFILES.get(profile, {}))
    cfg["profit_control_profile"] = profile
    return cfg


def _leader_rows(app):
    # Use corrected-accounting epoch when available so pre-fix P&L cannot poison
    # the learning registry. This mirrors the existing copied-performance guard.
    try:
        from . import solana_profit_accounting_epoch_patch as _epoch
        cutoff = int(_epoch._epoch(app))
    except Exception:
        cutoff = 0
    with closing(_sol.connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT telegram_id,leader_wallet,realised_net_sol,closed_at
               FROM positions
               WHERE status='CLOSED' AND mode='LIVE' AND COALESCE(closed_at,0)>=?
               ORDER BY closed_at DESC LIMIT 5000""",
            (cutoff,),
        ).fetchall()]
    return rows


def _refresh_leader_registry(app, now: int):
    grouped = defaultdict(list)
    for row in _leader_rows(app):
        key = (str(row.get("telegram_id") or ""), str(row.get("leader_wallet") or ""))
        if not key[0] or not key[1] or len(grouped[key]) >= 50:
            continue
        grouped[key].append(row)

    records = []
    with closing(_connect(app)) as conn:
        for (tid, wallet), rows in grouped.items():
            vals = [_d(r.get("realised_net_sol"), 0) for r in rows]
            profit = sum((v for v in vals if v > 0), Decimal(0))
            loss = sum((-v for v in vals if v < 0), Decimal(0))
            wins = sum(1 for v in vals if v > 0)
            losses = sum(1 for v in vals if v < 0)
            closed = len(vals)
            net = profit - loss
            pf = _pf(profit, loss)
            last_closed = max((int(r.get("closed_at") or 0) for r in rows), default=0)
            successful = _is_success(wins, losses, net, pf, min_trades=MIN_LEADER_TRADES, closed=closed)
            # A losing leader is cooled down relative to its latest actual copy.
            # After expiry it gets one controlled opportunity to prove recovery.
            blocked_until = 0
            if closed >= MIN_LEADER_TRADES and not successful:
                blocked_until = last_closed + LEADER_RETRY_COOLDOWN_SECONDS
            conn.execute(
                """INSERT INTO leader_registry(
                     telegram_id,leader_wallet,closed_trades,wins,losses,gross_profit_sol,gross_loss_sol,
                     net_sol,profit_factor,successful,blocked_until,last_closed_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(telegram_id,leader_wallet) DO UPDATE SET
                     closed_trades=excluded.closed_trades,wins=excluded.wins,losses=excluded.losses,
                     gross_profit_sol=excluded.gross_profit_sol,gross_loss_sol=excluded.gross_loss_sol,
                     net_sol=excluded.net_sol,profit_factor=excluded.profit_factor,
                     successful=excluded.successful,blocked_until=excluded.blocked_until,
                     last_closed_at=excluded.last_closed_at,updated_at=excluded.updated_at""",
                (tid, wallet, closed, wins, losses, str(profit), str(loss), str(net), str(pf),
                 1 if successful else 0, int(blocked_until), int(last_closed), int(now)),
            )
            records.append({
                "telegram_id": tid, "leader_wallet": wallet, "closed_trades": closed,
                "wins": wins, "losses": losses, "net_sol": str(net),
                "profit_factor": str(pf), "successful": bool(successful),
                "blocked_until": int(blocked_until), "last_closed_at": int(last_closed),
            })
        conn.commit()
    return records


def _leader_control_row(app, tid, wallet):
    try:
        with closing(_connect(app)) as conn:
            row = conn.execute(
                "SELECT * FROM leader_registry WHERE telegram_id=? AND leader_wallet=?",
                (str(tid), str(wallet)),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def copied_ok_with_profit_control(app, tid, wallet, cfg):
    row = _leader_control_row(app, tid, wallet)
    if row and int(row.get("blocked_until") or 0) > int(time.time()):
        return False
    return _PREV_COPIED_OK(app, tid, wallet, cfg)


def _interval_rows(app, since: int, now: int):
    with closing(_sol.connect(app)) as conn:
        return [dict(r) for r in conn.execute(
            """SELECT telegram_id,leader_wallet,realised_net_sol,closed_at
               FROM positions
               WHERE status='CLOSED' AND mode='LIVE'
                 AND COALESCE(closed_at,0)>? AND COALESCE(closed_at,0)<=?
               ORDER BY closed_at""",
            (int(since), int(now)),
        ).fetchall()]


def _stats(rows):
    vals = [_d(r.get("realised_net_sol"), 0) for r in rows]
    profit = sum((v for v in vals if v > 0), Decimal(0))
    loss = sum((-v for v in vals if v < 0), Decimal(0))
    wins = sum(1 for v in vals if v > 0)
    losses = sum(1 for v in vals if v < 0)
    closed = len(vals)
    net = profit - loss
    return {
        "closed": closed, "wins": wins, "losses": losses,
        "profit": profit, "loss": loss, "net": net, "profit_factor": _pf(profit, loss),
    }


def _update_strategy_registry(conn, profile: str, stats: dict, now: int):
    old = conn.execute("SELECT * FROM strategy_registry WHERE profile=?", (profile,)).fetchone()
    hours = int(old["hours_observed"] or 0) + 1 if old else 1
    closed = int(old["closed_trades"] or 0) + int(stats["closed"]) if old else int(stats["closed"])
    wins = int(old["wins"] or 0) + int(stats["wins"]) if old else int(stats["wins"])
    losses = int(old["losses"] or 0) + int(stats["losses"]) if old else int(stats["losses"])
    profit = _d(old["gross_profit_sol"], 0) + stats["profit"] if old else stats["profit"]
    loss = _d(old["gross_loss_sol"], 0) + stats["loss"] if old else stats["loss"]
    net = profit - loss
    pf = _pf(profit, loss)
    successful = _is_success(wins, losses, net, pf, min_trades=MIN_PROFILE_TRADES, closed=closed)
    conn.execute(
        """INSERT INTO strategy_registry(
             profile,hours_observed,closed_trades,wins,losses,gross_profit_sol,gross_loss_sol,
             net_sol,profit_factor,successful,last_used_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(profile) DO UPDATE SET
             hours_observed=excluded.hours_observed,closed_trades=excluded.closed_trades,
             wins=excluded.wins,losses=excluded.losses,gross_profit_sol=excluded.gross_profit_sol,
             gross_loss_sol=excluded.gross_loss_sol,net_sol=excluded.net_sol,
             profit_factor=excluded.profit_factor,successful=excluded.successful,
             last_used_at=excluded.last_used_at,updated_at=excluded.updated_at""",
        (profile, hours, closed, wins, losses, str(profit), str(loss), str(net), str(pf),
         1 if successful else 0, now, now),
    )
    return {
        "profile": profile, "hours_observed": hours, "closed_trades": closed,
        "wins": wins, "losses": losses, "net_sol": str(net),
        "profit_factor": str(pf), "successful": successful,
    }


def _choose_next_profile(conn, current: str, current_row: dict, zero_trade_hours: int):
    # Successful policy stays in place. Do not change policy on a single hour.
    if current_row.get("successful"):
        return current
    if int(current_row.get("hours_observed") or 0) < MIN_PROFILE_HOURS:
        return current
    if int(current_row.get("closed_trades") or 0) < MIN_PROFILE_TRADES:
        # A strict profile that starves trading for three hours falls back to the
        # baseline so the loop can gather evidence again.
        if zero_trade_hours >= 3 and current != "BASELINE":
            return "BASELINE"
        return current

    # First prefer a previously proven profitable profile.
    proven = [dict(r) for r in conn.execute(
        "SELECT * FROM strategy_registry WHERE successful=1 ORDER BY CAST(net_sol AS REAL) DESC, CAST(profit_factor AS REAL) DESC"
    ).fetchall()]
    if proven:
        best = str(proven[0].get("profile") or "BASELINE")
        if best in PROFILES and best != current:
            return best

    # No proven winner yet: progress through bounded stricter entry policies.
    idx = PROFILE_ORDER.index(current) if current in PROFILE_ORDER else 0
    return PROFILE_ORDER[(idx + 1) % len(PROFILE_ORDER)]


def _export_registry(app):
    root = Path(app.data_dir) / "profit_control_loop"
    root.mkdir(parents=True, exist_ok=True)
    with closing(_connect(app)) as conn:
        leaders = [dict(r) for r in conn.execute(
            "SELECT * FROM leader_registry ORDER BY successful DESC, CAST(net_sol AS REAL) DESC, CAST(profit_factor AS REAL) DESC"
        ).fetchall()]
        strategies = [dict(r) for r in conn.execute(
            "SELECT * FROM strategy_registry ORDER BY successful DESC, CAST(net_sol AS REAL) DESC, CAST(profit_factor AS REAL) DESC"
        ).fetchall()]
    for name, rows in (("successful_leaders.csv", leaders), ("successful_strategies.csv", strategies)):
        path = root / name
        headers = list(rows[0].keys()) if rows else (["telegram_id", "leader_wallet"] if "leaders" in name else ["profile"])
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for row in rows:
                w.writerow({h: row.get(h, "") for h in headers})
        tmp.replace(path)
    return root


def run_profit_control_loop(app, gpt_result: dict | None = None) -> dict:
    now = int(time.time())
    previous = active_profile(app)
    with closing(_connect(app)) as conn:
        last_eval = int(_get_state(conn, "last_eval_epoch", str(now - 3600)) or (now - 3600))
        # Keep a bounded interval even after a long service outage; old results are
        # still preserved in leader lifetime metrics but not attributed to one hour.
        since = max(last_eval, now - 2 * 3600)
        rows = _interval_rows(app, since, now)
        hourly = _stats(rows)
        zero_hours = int(_get_state(conn, "zero_trade_hours", "0") or 0)
        zero_hours = zero_hours + 1 if hourly["closed"] == 0 else 0
        strategy = _update_strategy_registry(conn, previous, hourly, now)
        next_profile = _choose_next_profile(conn, previous, strategy, zero_hours)
        changed = next_profile != previous
        _set_state(conn, "active_profile", next_profile)
        _set_state(conn, "last_eval_epoch", now)
        _set_state(conn, "zero_trade_hours", zero_hours)
        gpt_status = str(((gpt_result or {}).get("review") or {}).get("status") or ("ERROR" if gpt_result and not gpt_result.get("ok") else ""))
        conn.execute(
            """INSERT INTO control_runs(
                 generated_at,profile,closed_trades,wins,losses,gross_profit_sol,gross_loss_sol,
                 net_sol,profit_factor,profile_changed,previous_profile,gpt_status,details_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now, next_profile, hourly["closed"], hourly["wins"], hourly["losses"],
             str(hourly["profit"]), str(hourly["loss"]), str(hourly["net"]), str(hourly["profit_factor"]),
             1 if changed else 0, previous, gpt_status,
             json.dumps({"source": "hourly_live_closed_positions", "zero_trade_hours": zero_hours}, separators=(",", ":"))),
        )
        conn.commit()

    leader_records = _refresh_leader_registry(app, now)
    # Re-run leader ranking after the registry is refreshed. The copied_ok wrapper
    # removes leaders still inside their timed losing cooldown and fills from the
    # next otherwise-qualified leader.
    try:
        _sol.refresh_rankings(app)
    except Exception as exc:
        ranking_error = f"{type(exc).__name__}: {exc}"
    else:
        ranking_error = ""

    root = _export_registry(app)
    successful_leaders = [r for r in leader_records if r["successful"]]
    blocked_leaders = [r for r in leader_records if int(r["blocked_until"]) > now]
    result = {
        "generated_at": now,
        "previous_profile": previous,
        "active_profile": next_profile,
        "profile_changed": changed,
        "hour": {
            "closed_trades": hourly["closed"], "wins": hourly["wins"], "losses": hourly["losses"],
            "net_sol": str(hourly["net"]), "profit_factor": str(hourly["profit_factor"]),
        },
        "strategy_registry": strategy,
        "successful_leaders": len(successful_leaders),
        "blocked_leaders": len(blocked_leaders),
        "leader_records": leader_records[:100],
        "ranking_error": ranking_error,
        "records_dir": str(root),
        "objective": "wins>losses AND net_realised_pnl>0 AND profit_factor>1.10",
        "live_armed_state_changed": False,
        "capital_or_safety_controls_changed": False,
    }
    latest = root / "latest_control_loop.json"
    tmp = latest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(latest)
    return result


def run_hourly_gpt_review_with_control(app, zip_path):
    result = _ORIGINAL_GPT_REVIEW(app, zip_path)
    try:
        result["control_loop"] = run_profit_control_loop(app, result)
    except Exception as exc:
        result["control_loop"] = {
            "error": f"{type(exc).__name__}: {exc}",
            "live_armed_state_changed": False,
        }
    return result


def install():
    if getattr(_sol, "_profit_control_loop_installed", False):
        return
    _sol.settings = settings_with_profit_control
    _guard._copied_ok = copied_ok_with_profit_control
    _worker.run_hourly_gpt_review = run_hourly_gpt_review_with_control
    _sol._profit_control_loop_installed = True
    print(
        "[profit-control-loop] hourly=true objective=wins_gt_losses+positive_net+pf_gt_1.10 "
        "leader_memory=true strategy_memory=true bounded_profiles=true live_arm_unchanged=true"
    )


install()
