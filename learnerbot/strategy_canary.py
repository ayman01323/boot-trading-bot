from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .ai_ops_status import fetch_ai_reviews, master_chat_ids, read_json
from .config import load_kv_scoped
from .cross_chain_strategy_signals import evaluate_all
from .market_feature_adapter import adapt_evm_opportunity

# Fast CANARY is a bounded real-money validation lane, not permission for an AI model
# to bypass the live executor.  The only routes considered are the bot's existing exact
# EVM scanner routes, and the existing auto-trader still performs its normal user-specific
# re-quote/simulation and signing safeguards after this module returns a policy.

MIN_MASTER_CONFIDENCE = Decimal("0.85")
ALLOWED_MASTER_RISKS = {"LOW", "MEDIUM"}
ALLOWED_MASTER_ACTIONS = {"KEEP", "IMPROVE", "NEW_SHADOW"}
MIN_SUPPORTING_AGENTS = 2
CANARY_TO_PROBATION_TRADES = 3
PROBATION_TO_ACTIVE_TRADES = 8
MIN_LIVE_PROFIT_FACTOR = Decimal("1.20")
APPROVAL_REFRESH_SECONDS = 60
APPROVAL_STALE_SECONDS = 15 * 60

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS strategy_canary_state(
  strategy TEXT PRIMARY KEY,
  cycle_id TEXT NOT NULL,
  source_commit TEXT NOT NULL,
  stage TEXT NOT NULL,
  trades INTEGER NOT NULL DEFAULT 0,
  wins INTEGER NOT NULL DEFAULT 0,
  losses INTEGER NOT NULL DEFAULT 0,
  gross_profit TEXT NOT NULL DEFAULT '0',
  gross_loss TEXT NOT NULL DEFAULT '0',
  net_profit TEXT NOT NULL DEFAULT '0',
  execution_failures INTEGER NOT NULL DEFAULT 0,
  consecutive_losses INTEGER NOT NULL DEFAULT 0,
  largest_loss TEXT NOT NULL DEFAULT '0',
  last_reason TEXT NOT NULL DEFAULT '',
  updated_at INTEGER NOT NULL
);
"""

_APPROVAL_CACHE: dict[str, Any] = {"checked": 0, "ok_at": 0, "approvals": {}, "cycle_id": "", "source_commit": ""}


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _db_path(app) -> Path:
    return Path(app.data_dir) / "strategy_canary.sqlite3"


def connect(app):
    path = _db_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _approval_rows(master: dict | None, status: dict | None) -> dict[str, dict]:
    if not isinstance(master, dict) or not isinstance(status, dict):
        return {}
    if status.get("three_agent_reports_complete") is not True or status.get("master_decision_available") is not True:
        return {}
    approvals: dict[str, dict] = {}
    for raw in master.get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("disposition") or "").upper() != "ACCEPT":
            continue
        if str(raw.get("action") or "").upper() not in ALLOWED_MASTER_ACTIONS:
            continue
        if raw.get("shadow_only") is not True:
            continue
        if str(raw.get("risk_class") or "").upper() not in ALLOWED_MASTER_RISKS:
            continue
        if _d(raw.get("confidence")) < MIN_MASTER_CONFIDENCE:
            continue
        agents = {str(x).lower() for x in (raw.get("supporting_agents") or []) if str(x).strip()}
        if len(agents & {"gpt", "gemini", "copilot"}) < MIN_SUPPORTING_AGENTS:
            continue
        strategy = str(raw.get("strategy") or "").strip()
        if not strategy:
            continue
        approvals[strategy.lower()] = {
            "strategy": strategy,
            "finding_id": str(raw.get("finding_id") or ""),
            "action": str(raw.get("action") or "").upper(),
            "confidence": str(_d(raw.get("confidence"))),
            "supporting_agents": sorted(agents),
            "risk_class": str(raw.get("risk_class") or "").upper(),
        }
    return approvals


def refresh_approvals(app=None, *, force: bool = False, now: int | None = None) -> dict:
    now = int(now or time.time())
    if not force and now - int(_APPROVAL_CACHE.get("checked") or 0) < APPROVAL_REFRESH_SECONDS:
        return dict(_APPROVAL_CACHE)
    ok, _detail = fetch_ai_reviews(_repo_root(), timeout=12)
    status = read_json(_repo_root(), "strategy/latest_status.json")
    master = read_json(_repo_root(), "strategy/latest_master_decision.json")
    approvals = _approval_rows(master, status)
    if ok:
        _APPROVAL_CACHE.update({
            "checked": now,
            "ok_at": now,
            "approvals": approvals,
            "cycle_id": str((status or {}).get("cycle_id") or (master or {}).get("cycle_id") or ""),
            "source_commit": str((status or {}).get("source_commit") or (master or {}).get("source_commit") or ""),
        })
    else:
        _APPROVAL_CACHE["checked"] = now
        if now - int(_APPROVAL_CACHE.get("ok_at") or 0) > APPROVAL_STALE_SECONDS:
            _APPROVAL_CACHE["approvals"] = {}
    return dict(_APPROVAL_CACHE)


def _state(app, strategy: str, approval: dict, *, now: int) -> dict:
    cycle_id = str(approval.get("cycle_id") or "")
    source_commit = str(approval.get("source_commit") or "")
    with closing(connect(app)) as conn:
        row = conn.execute("SELECT * FROM strategy_canary_state WHERE strategy=?", (strategy,)).fetchone()
        if row and str(row["source_commit"] or "") == source_commit:
            return dict(row)
        # A newly deployed/reviewed source begins again at tiny CANARY even if an older
        # version of the same strategy had reached PROBATION/ACTIVE.
        conn.execute(
            """INSERT INTO strategy_canary_state(strategy,cycle_id,source_commit,stage,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(strategy) DO UPDATE SET
                 cycle_id=excluded.cycle_id,source_commit=excluded.source_commit,stage='CANARY',
                 trades=0,wins=0,losses=0,gross_profit='0',gross_loss='0',net_profit='0',
                 execution_failures=0,consecutive_losses=0,largest_loss='0',last_reason='',updated_at=excluded.updated_at""",
            (strategy, cycle_id, source_commit, "CANARY", now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM strategy_canary_state WHERE strategy=?", (strategy,)).fetchone())


def _canary_input(app, chain_id: Any) -> Decimal:
    cfg = load_kv_scoped(Path(app.csv_dir) / "copy_settings.csv", chain_id)
    return max(Decimal(0), _d(cfg.get("canary_input_base"), "0.05"))


def route_canary_policy(app, row: dict, *, now: int | None = None) -> dict | None:
    """Return a bounded CANARY policy when an existing exact EVM route matches a gated strategy.

    Returning None never makes an otherwise-ineligible route executable.  The caller must still
    enforce all normal auto-trader/live-executor safeguards.
    """
    now = int(now or time.time())
    cache = refresh_approvals(app, now=now)
    approvals = dict(cache.get("approvals") or {})
    if not approvals:
        return None
    try:
        env = adapt_evm_opportunity(app, row, now=now, source_type="EVM:LIVE_CANARY")
        signals = [s for s in evaluate_all(env.features) if s.eligible and s.strategy.lower() in approvals]
    except Exception:
        return None
    if not signals or not env.outcome_available:
        return None
    signals.sort(key=lambda s: (_d(s.confidence), _d(s.score), _d(s.expected_net_edge_bps)), reverse=True)
    signal = signals[0]
    raw = dict(approvals[signal.strategy.lower()])
    approval = {
        **raw,
        "cycle_id": str(cache.get("cycle_id") or ""),
        "source_commit": str(cache.get("source_commit") or ""),
    }
    state = _state(app, signal.strategy, approval, now=now)
    stage = str(state.get("stage") or "CANARY").upper()
    if stage == "PAUSED":
        return None
    base_cap = _canary_input(app, row.get("chain_id") or 0)
    input_cap = base_cap if stage == "CANARY" else base_cap * Decimal(2) if stage == "PROBATION" else Decimal(0)
    return {
        "strategy": signal.strategy,
        "stage": stage,
        "cycle_id": approval["cycle_id"],
        "source_commit": approval["source_commit"],
        "confidence": str(signal.confidence),
        "expected_net_edge_bps": str(signal.expected_net_edge_bps),
        "input_cap_base": str(input_cap),
        "full_size_allowed": stage == "ACTIVE",
        "master_action": raw.get("action"),
    }


def _notify(app, text: str) -> None:
    try:
        token = str(getattr(app, "telegram_bot_token", "") or "")
        ids = master_chat_ids(Path(app.csv_dir))
        if token and ids:
            from .telegram import send_to_chats
            send_to_chats(token, ids, text, disable_notification=False)
    except Exception as exc:
        print(f"[strategy-canary-telegram] {type(exc).__name__}: {exc}")


def record_canary_result(
    app,
    policy: dict | None,
    *,
    realised_net_base: Any | None = None,
    execution_failure: bool = False,
    reason: str = "",
    now: int | None = None,
) -> dict | None:
    if not policy or not str(policy.get("strategy") or "").strip():
        return None
    now = int(now or time.time())
    strategy = str(policy["strategy"])
    cycle_id = str(policy.get("cycle_id") or "")
    source_commit = str(policy.get("source_commit") or "")
    with closing(connect(app)) as conn:
        row = conn.execute("SELECT * FROM strategy_canary_state WHERE strategy=?", (strategy,)).fetchone()
        if not row:
            return None
        old = dict(row)
        stage = str(old["stage"] or "CANARY").upper()
        trades = int(old["trades"] or 0)
        wins = int(old["wins"] or 0)
        losses = int(old["losses"] or 0)
        gp = _d(old["gross_profit"])
        gl = _d(old["gross_loss"])
        net = _d(old["net_profit"])
        failures = int(old["execution_failures"] or 0)
        consecutive = int(old["consecutive_losses"] or 0)
        largest_loss = _d(old["largest_loss"])

        if execution_failure:
            failures += 1
            if failures >= 2:
                stage = "PAUSED"
        elif realised_net_base is not None:
            value = _d(realised_net_base)
            trades += 1
            net += value
            if value > 0:
                wins += 1
                gp += value
                consecutive = 0
            elif value < 0:
                losses += 1
                loss = abs(value)
                gl += loss
                largest_loss = max(largest_loss, loss)
                consecutive += 1
            else:
                consecutive = 0

            pf = gp / gl if gl > 0 else (Decimal("99") if gp > 0 else Decimal(0))
            # Loss containment comes before promotion. Two consecutive losses, or an
            # unprofitable two-trade CANARY sample, pauses the strategy for the next AI review.
            if consecutive >= 2 or (trades >= 2 and net <= 0):
                stage = "PAUSED"
            elif stage == "CANARY" and trades >= CANARY_TO_PROBATION_TRADES and net > 0 and pf >= MIN_LIVE_PROFIT_FACTOR and failures == 0:
                stage = "PROBATION"
            elif stage == "PROBATION" and trades >= PROBATION_TO_ACTIVE_TRADES and net > 0 and pf >= MIN_LIVE_PROFIT_FACTOR and failures <= 1:
                stage = "ACTIVE"

        conn.execute(
            """UPDATE strategy_canary_state SET cycle_id=?,source_commit=?,stage=?,trades=?,wins=?,losses=?,
               gross_profit=?,gross_loss=?,net_profit=?,execution_failures=?,consecutive_losses=?,largest_loss=?,
               last_reason=?,updated_at=? WHERE strategy=?""",
            (cycle_id, source_commit, stage, trades, wins, losses, str(gp), str(gl), str(net), failures,
             consecutive, str(largest_loss), str(reason or "")[:500], now, strategy),
        )
        conn.commit()

    if stage != str(old.get("stage") or "CANARY").upper():
        _notify(
            app,
            "🧪 STRATEGY LIVE STAGE UPDATE\n"
            f"Strategy: {strategy}\n"
            f"{old.get('stage')} → {stage}\n"
            f"Realised trades: {trades} | wins {wins} | losses {losses}\n"
            f"Realised net: {net}\n"
            + ("⛔ New entries are paused pending the next review." if stage == "PAUSED" else "Existing live safety gates remain mandatory."),
        )
    return {
        "strategy": strategy,
        "stage": stage,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "gross_profit": str(gp),
        "gross_loss": str(gl),
        "net_profit": str(net),
        "execution_failures": failures,
        "largest_loss": str(largest_loss),
    }


def canary_status(app) -> list[dict]:
    try:
        with closing(connect(app)) as conn:
            rows = conn.execute("SELECT * FROM strategy_canary_state ORDER BY updated_at DESC, strategy").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
