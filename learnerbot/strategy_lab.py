from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# Strategy Laboratory
# -------------------
# This module deliberately separates strategy discovery/evaluation from LIVE execution.
# AI agents and the learning bot may propose creative strategies, but every new proposal
# starts in SHADOW.  A later execution adapter may use a PROMOTION_CANDIDATE only after
# explicit tests/approval.  This module never arms LIVE trading, changes capital, signs a
# transaction, or relaxes an execution safety control.

STRATEGY_STATUSES = {
    "PROPOSED",
    "SHADOW",
    "ACTIVE",
    "PROBATION",
    "PROMOTION_CANDIDATE",
    "REWORK",
    "REPLACE",
    "RETIRED",
}

STRATEGY_SOURCES = {
    "LEADER_COPY",
    "LEARNED_PATTERN",
    "AI_PROPOSED",
    "MARKET_NATIVE",
    "OPERATOR",
}

# Defaults are research governance thresholds, not promises of profitability.
# They are intentionally less concerned with raw trade frequency than with whether a
# strategy actually participates when *its own* edge rule says an opportunity is valid.
MIN_EVALUATION_WINDOWS = 3
MIN_EVALUATION_TRADES = 8
MIN_ELIGIBLE_OPPORTUNITIES = 10
MIN_ELIGIBLE_PARTICIPATION = Decimal("0.20")
MIN_PROFIT_FACTOR = Decimal("1.10")

# An AI strategy description is data, not executable code.  Reject obvious credential /
# infrastructure material so an AI proposal cannot smuggle operational secrets or a
# deployment change through the strategy registry.
FORBIDDEN_SPEC_TERMS = {
    "private_key",
    "seed_phrase",
    "mnemonic",
    "signing_key",
    "telegram_bot_token",
    "api_key",
    "rpc_password",
    "deploy_vps",
    "live_auto_deploy",
}

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS strategy_lab_registry(
  strategy_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL,
  name TEXT NOT NULL,
  family TEXT NOT NULL,
  source TEXT NOT NULL,
  hypothesis TEXT NOT NULL,
  status TEXT NOT NULL,
  params_json TEXT NOT NULL,
  proposed_by TEXT NOT NULL,
  replacement_of TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_lab_windows(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  window_end INTEGER NOT NULL,
  mode TEXT NOT NULL,
  opportunities INTEGER NOT NULL DEFAULT 0,
  eligible_opportunities INTEGER NOT NULL DEFAULT 0,
  trades INTEGER NOT NULL DEFAULT 0,
  wins INTEGER NOT NULL DEFAULT 0,
  losses INTEGER NOT NULL DEFAULT 0,
  gross_profit TEXT NOT NULL DEFAULT '0',
  gross_loss TEXT NOT NULL DEFAULT '0',
  fees TEXT NOT NULL DEFAULT '0',
  slippage_cost TEXT NOT NULL DEFAULT '0',
  net_profit TEXT NOT NULL DEFAULT '0',
  largest_loss TEXT NOT NULL DEFAULT '0',
  execution_failures INTEGER NOT NULL DEFAULT 0,
  signal_skips INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  UNIQUE(strategy_id,window_start,window_end,mode)
);

CREATE TABLE IF NOT EXISTS strategy_lab_decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT NOT NULL,
  generated_at INTEGER NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  metrics_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_lab_windows_strategy
ON strategy_lab_windows(strategy_id,window_end);
"""


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _pf(gross_profit: Decimal, gross_loss: Decimal) -> Decimal:
    if gross_loss > 0:
        return gross_profit / gross_loss
    return Decimal("99") if gross_profit > 0 else Decimal(0)


def _db_path(app) -> Path:
    return Path(app.data_dir) / "strategy_lab.sqlite3"


def connect(app):
    path = _db_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _safe_spec(spec: dict) -> None:
    text = _canonical_json(spec).lower()
    bad = sorted(term for term in FORBIDDEN_SPEC_TERMS if term in text)
    if bad:
        raise ValueError("strategy proposal contains forbidden operational fields: " + ", ".join(bad))


def _strategy_id(name: str, family: str, hypothesis: str) -> str:
    raw = f"{name.strip().lower()}|{family.strip().lower()}|{hypothesis.strip()}"
    return "strat_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def register_strategy(
    app,
    *,
    name: str,
    family: str,
    source: str,
    hypothesis: str,
    params: dict | None = None,
    proposed_by: str = "learning_bot",
    replacement_of: str | None = None,
) -> dict:
    """Register a strategy hypothesis.

    All new strategies start SHADOW, including operator- and AI-proposed strategies.
    Registration cannot make a strategy LIVE.
    """
    source = str(source or "").upper().strip()
    if source not in STRATEGY_SOURCES:
        raise ValueError(f"unsupported strategy source: {source}")
    if not str(name or "").strip() or not str(family or "").strip() or not str(hypothesis or "").strip():
        raise ValueError("name, family and hypothesis are required")
    spec = {
        "name": str(name).strip(),
        "family": str(family).strip(),
        "source": source,
        "hypothesis": str(hypothesis).strip(),
        "params": dict(params or {}),
        "proposed_by": str(proposed_by or "learning_bot").strip(),
        "replacement_of": str(replacement_of or "").strip() or None,
    }
    _safe_spec(spec)
    sid = _strategy_id(spec["name"], spec["family"], spec["hypothesis"])
    now = int(time.time())
    with closing(connect(app)) as conn:
        old = conn.execute("SELECT version FROM strategy_lab_registry WHERE strategy_id=?", (sid,)).fetchone()
        version = int(old["version"] or 0) + 1 if old else 1
        conn.execute(
            """INSERT INTO strategy_lab_registry(
                 strategy_id,version,name,family,source,hypothesis,status,params_json,
                 proposed_by,replacement_of,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(strategy_id) DO UPDATE SET
                 version=excluded.version,name=excluded.name,family=excluded.family,
                 source=excluded.source,hypothesis=excluded.hypothesis,status='SHADOW',
                 params_json=excluded.params_json,proposed_by=excluded.proposed_by,
                 replacement_of=excluded.replacement_of,updated_at=excluded.updated_at""",
            (
                sid,
                version,
                spec["name"],
                spec["family"],
                source,
                spec["hypothesis"],
                "SHADOW",
                _canonical_json(spec["params"]),
                spec["proposed_by"],
                spec["replacement_of"],
                now,
                now,
            ),
        )
        conn.commit()
    return {"strategy_id": sid, "version": version, "status": "SHADOW", **spec}


def seed_creative_hypotheses(app) -> list[dict]:
    """Install broad, asset-neutral research families if absent.

    These are hypotheses only.  They create no orders and do not bypass the normal
    positive-edge/liquidity/simulation/execution gates.
    """
    ideas = [
        {
            "name": "Cross Venue Net Arbitrage",
            "family": "ARBITRAGE",
            "hypothesis": "Trade only cross-venue price discrepancies whose executable spread remains positive after known fees, slippage, price impact and latency reserve.",
            "params": {"requires_net_edge": True, "atomicity_preferred": True},
        },
        {
            "name": "Liquidity Confirmed Momentum",
            "family": "MOMENTUM",
            "hypothesis": "Short-lived momentum is more reliable when price acceleration is confirmed by liquidity and transaction-flow expansion rather than price change alone.",
            "params": {"confirmation": ["price_acceleration", "liquidity", "flow"]},
        },
        {
            "name": "Dislocation Mean Reversion",
            "family": "MEAN_REVERSION",
            "hypothesis": "Temporary price dislocations that are not accompanied by structural liquidity deterioration can revert sufficiently to overcome execution costs.",
            "params": {"requires_liquidity_stability": True},
        },
        {
            "name": "Flow Acceleration",
            "family": "FLOW",
            "hypothesis": "Acceleration in independent transaction flow can identify opportunities before leader-copy latency, provided concentration and liquidity checks remain acceptable.",
            "params": {"avoid_single_wallet_dependency": True},
        },
        {
            "name": "New Liquidity Quality",
            "family": "NEW_MARKET",
            "hypothesis": "New pools/tokens can be ranked by liquidity quality, sellability, holder/flow dispersion and execution quality before any momentum entry is considered.",
            "params": {"requires_sellability": True, "requires_liquidity": True},
        },
        {
            "name": "Learned Route Replication",
            "family": "LEARNED_PATTERN",
            "hypothesis": "Repeated public route structures with proven positive net outcomes can be replay candidates even when no single leader wallet is followed.",
            "params": {"use_strategy_patterns": True, "require_proven_net": True},
        },
    ]
    out = []
    with closing(connect(app)) as conn:
        existing = {str(r["name"]) for r in conn.execute("SELECT name FROM strategy_lab_registry").fetchall()}
    for idea in ideas:
        if idea["name"] in existing:
            continue
        out.append(
            register_strategy(
                app,
                name=idea["name"],
                family=idea["family"],
                source="MARKET_NATIVE" if idea["family"] != "LEARNED_PATTERN" else "LEARNED_PATTERN",
                hypothesis=idea["hypothesis"],
                params=idea["params"],
                proposed_by="built_in_strategy_lab",
            )
        )
    return out


def record_window(
    app,
    strategy_id: str,
    *,
    window_start: int,
    window_end: int,
    mode: str,
    opportunities: int = 0,
    eligible_opportunities: int = 0,
    trades: int = 0,
    wins: int = 0,
    losses: int = 0,
    gross_profit: Any = 0,
    gross_loss: Any = 0,
    fees: Any = 0,
    slippage_cost: Any = 0,
    net_profit: Any | None = None,
    largest_loss: Any = 0,
    execution_failures: int = 0,
    signal_skips: int = 0,
    metadata: dict | None = None,
) -> None:
    """Record one strategy separately from all others.

    `eligible_opportunities` means opportunities that passed the strategy's own signal
    rule and common safety/executability prerequisites.  This lets the lab distinguish
    'no edge existed' from 'the strategy was so restrictive that it failed to act'.
    """
    if int(window_end) <= int(window_start):
        raise ValueError("window_end must be greater than window_start")
    if any(int(v) < 0 for v in (opportunities, eligible_opportunities, trades, wins, losses, execution_failures, signal_skips)):
        raise ValueError("counts cannot be negative")
    if int(eligible_opportunities) > int(opportunities):
        raise ValueError("eligible_opportunities cannot exceed opportunities")
    if int(trades) > int(eligible_opportunities):
        raise ValueError("trades cannot exceed eligible_opportunities")
    if int(wins) + int(losses) > int(trades):
        raise ValueError("wins + losses cannot exceed trades")

    gp = _d(gross_profit)
    gl = _d(gross_loss)
    fee = _d(fees)
    slip = _d(slippage_cost)
    net = _d(net_profit) if net_profit is not None else gp - gl - fee - slip
    now = int(time.time())
    with closing(connect(app)) as conn:
        if not conn.execute("SELECT 1 FROM strategy_lab_registry WHERE strategy_id=?", (str(strategy_id),)).fetchone():
            raise ValueError(f"unknown strategy_id: {strategy_id}")
        conn.execute(
            """INSERT INTO strategy_lab_windows(
                 strategy_id,window_start,window_end,mode,opportunities,eligible_opportunities,
                 trades,wins,losses,gross_profit,gross_loss,fees,slippage_cost,net_profit,
                 largest_loss,execution_failures,signal_skips,metadata_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(strategy_id,window_start,window_end,mode) DO UPDATE SET
                 opportunities=excluded.opportunities,eligible_opportunities=excluded.eligible_opportunities,
                 trades=excluded.trades,wins=excluded.wins,losses=excluded.losses,
                 gross_profit=excluded.gross_profit,gross_loss=excluded.gross_loss,
                 fees=excluded.fees,slippage_cost=excluded.slippage_cost,
                 net_profit=excluded.net_profit,largest_loss=excluded.largest_loss,
                 execution_failures=excluded.execution_failures,signal_skips=excluded.signal_skips,
                 metadata_json=excluded.metadata_json,created_at=excluded.created_at""",
            (
                str(strategy_id), int(window_start), int(window_end), str(mode).upper(),
                int(opportunities), int(eligible_opportunities), int(trades), int(wins), int(losses),
                str(gp), str(gl), str(fee), str(slip), str(net), str(_d(largest_loss)),
                int(execution_failures), int(signal_skips), _canonical_json(metadata or {}), now,
            ),
        )
        conn.commit()


def strategy_metrics(app, strategy_id: str, *, mode: str | None = None) -> dict:
    sql = "SELECT * FROM strategy_lab_windows WHERE strategy_id=?"
    args: list[Any] = [str(strategy_id)]
    if mode:
        sql += " AND mode=?"
        args.append(str(mode).upper())
    sql += " ORDER BY window_end"
    with closing(connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
    gross_profit = sum((_d(r["gross_profit"]) for r in rows), Decimal(0))
    gross_loss = sum((_d(r["gross_loss"]) for r in rows), Decimal(0))
    fees = sum((_d(r["fees"]) for r in rows), Decimal(0))
    slippage = sum((_d(r["slippage_cost"]) for r in rows), Decimal(0))
    net = sum((_d(r["net_profit"]) for r in rows), Decimal(0))
    eligible = sum(int(r["eligible_opportunities"] or 0) for r in rows)
    trades = sum(int(r["trades"] or 0) for r in rows)
    participation = Decimal(trades) / Decimal(eligible) if eligible else Decimal(0)
    return {
        "windows": len(rows),
        "opportunities": sum(int(r["opportunities"] or 0) for r in rows),
        "eligible_opportunities": eligible,
        "trades": trades,
        "wins": sum(int(r["wins"] or 0) for r in rows),
        "losses": sum(int(r["losses"] or 0) for r in rows),
        "gross_profit": str(gross_profit),
        "gross_loss": str(gross_loss),
        "fees": str(fees),
        "slippage_cost": str(slippage),
        "net_profit": str(net),
        "profit_factor": str(_pf(gross_profit, gross_loss)),
        "eligible_participation": str(participation),
        "largest_loss": str(max((_d(r["largest_loss"]) for r in rows), default=Decimal(0))),
        "execution_failures": sum(int(r["execution_failures"] or 0) for r in rows),
        "signal_skips": sum(int(r["signal_skips"] or 0) for r in rows),
    }


def evaluate_strategy(app, strategy_id: str, *, mode: str | None = None) -> dict:
    """Return an evidence-led lifecycle decision without changing LIVE execution."""
    with closing(connect(app)) as conn:
        row = conn.execute("SELECT * FROM strategy_lab_registry WHERE strategy_id=?", (str(strategy_id),)).fetchone()
        if not row:
            raise ValueError(f"unknown strategy_id: {strategy_id}")
        registry = dict(row)

    m = strategy_metrics(app, strategy_id, mode=mode)
    windows = int(m["windows"])
    trades = int(m["trades"])
    eligible = int(m["eligible_opportunities"])
    participation = _d(m["eligible_participation"])
    net = _d(m["net_profit"])
    pf = _d(m["profit_factor"])

    # Do not punish a strategy simply because the market offered no eligible setup.
    if windows < MIN_EVALUATION_WINDOWS:
        action = "KEEP_TESTING"
        status = "SHADOW" if registry["status"] in {"PROPOSED", "SHADOW"} else "PROBATION"
        reason = "insufficient independent observation windows"
    elif eligible == 0:
        action = "KEEP_SCANNING"
        status = "PROBATION"
        reason = "no eligible opportunities observed; inactivity alone is not evidence of failure"
    elif eligible >= MIN_ELIGIBLE_OPPORTUNITIES and participation < MIN_ELIGIBLE_PARTICIPATION:
        action = "REWORK_FILTERS"
        status = "REWORK"
        reason = "strategy found eligible opportunities but participated too rarely; review overly restrictive filters"
    elif trades < MIN_EVALUATION_TRADES:
        action = "KEEP_TESTING"
        status = "PROBATION"
        reason = "not enough executed/simulated trades for a replacement decision"
    elif net <= 0 or pf <= Decimal(1):
        action = "REPLACE_OR_REWORK"
        status = "REPLACE"
        reason = "adequate sample is money-weighted unprofitable after recorded costs"
    elif net > 0 and pf >= MIN_PROFIT_FACTOR:
        action = "PROMOTION_CANDIDATE"
        status = "PROMOTION_CANDIDATE"
        reason = "positive net result and profit factor passed research threshold; further out-of-sample and execution validation still required"
    else:
        action = "KEEP_TESTING"
        status = "PROBATION"
        reason = "positive result exists but margin of evidence remains weak"

    decision = {
        "strategy_id": str(strategy_id),
        "name": registry["name"],
        "family": registry["family"],
        "source": registry["source"],
        "previous_status": registry["status"],
        "status": status,
        "action": action,
        "reason": reason,
        "metrics": m,
        "live_auto_promote": False,
        "changes_capital_or_safety": False,
    }
    now = int(time.time())
    with closing(connect(app)) as conn:
        conn.execute(
            "UPDATE strategy_lab_registry SET status=?,updated_at=? WHERE strategy_id=?",
            (status, now, str(strategy_id)),
        )
        conn.execute(
            "INSERT INTO strategy_lab_decisions(strategy_id,generated_at,action,reason,metrics_json) VALUES(?,?,?,?,?)",
            (str(strategy_id), now, action, reason, _canonical_json(m)),
        )
        conn.commit()
    return decision


def portfolio_report(app, *, mode: str | None = None) -> dict:
    with closing(connect(app)) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM strategy_lab_registry ORDER BY name").fetchall()]
    strategies = []
    totals = {"strategies": len(rows), "promotion_candidates": 0, "replace": 0, "rework": 0}
    for row in rows:
        decision = evaluate_strategy(app, row["strategy_id"], mode=mode)
        strategies.append(decision)
        if decision["status"] == "PROMOTION_CANDIDATE":
            totals["promotion_candidates"] += 1
        elif decision["status"] == "REPLACE":
            totals["replace"] += 1
        elif decision["status"] == "REWORK":
            totals["rework"] += 1
    return {
        "generated_at": int(time.time()),
        "mode": str(mode or "ALL").upper(),
        "totals": totals,
        "strategies": strategies,
        "principle": "active strategies must scan and participate in eligible positive-edge opportunities; they are never forced to manufacture a trade when no eligible edge exists",
        "live_auto_promote": False,
    }
