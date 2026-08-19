from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from .cross_chain_strategy_signals import evaluate_all
from .market_feature_adapter import AdaptedMarketFeature, load_market_features


# This executor is deliberately NON-SIGNING. It evaluates current market observations,
# persists SHADOW signals, and measures executable quote/simulation economics where the
# existing scanner supplied them. Quote simulations are NOT promotion evidence: a later
# realised/out-of-sample outcome layer must prove profitability before CANARY/LIVE.

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS shadow_strategy_events(
  event_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  observed_at INTEGER NOT NULL,
  chain_type TEXT NOT NULL,
  chain_slug TEXT NOT NULL,
  asset_ref TEXT NOT NULL,
  strategy TEXT NOT NULL,
  eligible INTEGER NOT NULL,
  score TEXT NOT NULL,
  confidence TEXT NOT NULL,
  expected_net_edge_bps TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  outcome_available INTEGER NOT NULL DEFAULT 0,
  outcome_basis TEXT NOT NULL,
  notional_base TEXT NOT NULL DEFAULT '0',
  quote_simulated_net_base TEXT NOT NULL DEFAULT '0',
  gross_profit_base TEXT NOT NULL DEFAULT '0',
  fee_base TEXT NOT NULL DEFAULT '0',
  slippage_base TEXT NOT NULL DEFAULT '0',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  promotion_evidence INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shadow_strategy_time
  ON shadow_strategy_events(observed_at,strategy,chain_slug);
CREATE INDEX IF NOT EXISTS idx_shadow_strategy_source
  ON shadow_strategy_events(source_id,strategy);
"""


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _db_path(app) -> Path:
    return Path(app.data_dir) / "strategy_shadow.sqlite3"


def connect(app):
    path = _db_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _event_id(source_id: str, strategy: str) -> str:
    raw = f"{source_id}|{strategy}"
    return "shadow_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]


def evaluate_envelopes(app, envelopes: Iterable[AdaptedMarketFeature], *, now: int | None = None) -> dict:
    now = int(now or time.time())
    inserted = 0
    duplicate = 0
    eligible = 0
    executable_simulations = 0
    chains: dict[str, int] = defaultdict(int)
    with closing(connect(app)) as conn:
        for env in envelopes:
            for signal in evaluate_all(env.features):
                chains[signal.chain_slug] += 1
                is_eligible = bool(signal.eligible)
                eligible += int(is_eligible)
                quote_simulated_net = Decimal(0)
                outcome_available = bool(env.outcome_available and is_eligible)
                if outcome_available and env.notional_base > 0:
                    quote_simulated_net = env.notional_base * _d(signal.expected_net_edge_bps) / Decimal(10000)
                    executable_simulations += 1
                eid = _event_id(env.source_id, signal.strategy)
                metadata = dict(env.metadata or {})
                metadata.update({
                    "signal_mode": signal.mode,
                    "promotion_evidence": False,
                    "note": "Current quote/simulation economics only; not realised P&L and never sufficient for live promotion.",
                })
                before = conn.total_changes
                conn.execute(
                    """INSERT OR IGNORE INTO shadow_strategy_events(
                         event_id,source_id,source_type,observed_at,chain_type,chain_slug,asset_ref,
                         strategy,eligible,score,confidence,expected_net_edge_bps,reasons_json,
                         outcome_available,outcome_basis,notional_base,quote_simulated_net_base,
                         gross_profit_base,fee_base,slippage_base,metadata_json,promotion_evidence,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        eid,
                        env.source_id,
                        env.source_type,
                        int(env.features.observed_at or now),
                        signal.chain_type,
                        signal.chain_slug,
                        signal.asset,
                        signal.strategy,
                        int(is_eligible),
                        str(signal.score),
                        str(signal.confidence),
                        str(signal.expected_net_edge_bps),
                        json.dumps(list(signal.reasons), separators=(",", ":")),
                        int(outcome_available),
                        str(env.outcome_basis),
                        str(env.notional_base),
                        str(quote_simulated_net),
                        str(env.gross_profit_base),
                        str(env.fee_base),
                        str(env.slippage_base),
                        json.dumps(metadata, sort_keys=True, separators=(",", ":"), default=str),
                        0,
                        now,
                    ),
                )
                if conn.total_changes > before:
                    inserted += 1
                else:
                    duplicate += 1
        conn.commit()
    return {
        "inserted": inserted,
        "duplicates_ignored": duplicate,
        "eligible_signals_seen": eligible,
        "executable_quote_simulations_seen": executable_simulations,
        "evaluation_counts_by_chain": dict(chains),
        "live_orders_submitted": 0,
        "promotion_evidence_created": 0,
    }


def scorecard(app, *, since: int | None = None, now: int | None = None) -> dict:
    now = int(now or time.time())
    since = int(since if since is not None else now - 3600)
    with closing(connect(app)) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM shadow_strategy_events WHERE observed_at>=? ORDER BY observed_at",
            (since,),
        ).fetchall()]
    by_strategy: dict[str, dict] = {}
    by_chain: dict[str, dict] = {}
    gaps: dict[str, int] = defaultdict(int)
    for row in rows:
        name = str(row["strategy"])
        s = by_strategy.setdefault(name, {
            "opportunities": 0,
            "eligible_signals": 0,
            "executable_quote_simulations": 0,
            "quote_simulated_net_base": Decimal(0),
            "positive_quote_simulations": 0,
            "negative_quote_simulations": 0,
            "chains": defaultdict(int),
            "promotion_evidence": 0,
        })
        s["opportunities"] += 1
        s["eligible_signals"] += int(row["eligible"] or 0)
        s["chains"][str(row["chain_slug"])] += 1
        if int(row["outcome_available"] or 0):
            s["executable_quote_simulations"] += 1
            net = _d(row["quote_simulated_net_base"])
            s["quote_simulated_net_base"] += net
            s["positive_quote_simulations"] += int(net > 0)
            s["negative_quote_simulations"] += int(net < 0)
        s["promotion_evidence"] += int(row["promotion_evidence"] or 0)

        c = by_chain.setdefault(str(row["chain_slug"]), {
            "chain_type": str(row["chain_type"]),
            "evaluations": 0,
            "eligible_signals": 0,
            "executable_quote_simulations": 0,
        })
        c["evaluations"] += 1
        c["eligible_signals"] += int(row["eligible"] or 0)
        c["executable_quote_simulations"] += int(row["outcome_available"] or 0)

        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            meta = {}
        if meta.get("needs_quote_feature_adapter"):
            gaps["solana_current_quote_edge_adapter"] += 1
        try:
            reasons = json.loads(row["reasons_json"] or "[]")
        except Exception:
            reasons = []
        for reason in reasons:
            text = str(reason)
            if "liquidity_below" in text:
                gaps["liquidity_measurement_or_quality"] += 1
            elif "sellability_below" in text:
                gaps["sellability_measurement_or_quality"] += 1
            elif "quote_too_old" in text:
                gaps["quote_freshness"] += 1
            elif "forecast_" in text:
                gaps["forecast_model_or_calibration"] += 1

    serialised = {}
    for name, s in sorted(by_strategy.items()):
        serialised[name] = {
            **{k: v for k, v in s.items() if k not in {"quote_simulated_net_base", "chains"}},
            "quote_simulated_net_base": str(s["quote_simulated_net_base"]),
            "chains": dict(s["chains"]),
            "promotion_allowed_from_this_scorecard": False,
        }
    return {
        "generated_at": now,
        "window_start": since,
        "mode": "SHADOW",
        "strategy_scorecards": serialised,
        "chain_scorecards": by_chain,
        "evidence_gaps": dict(sorted(gaps.items(), key=lambda kv: (-kv[1], kv[0]))),
        "promotion_policy": (
            "Quote/simulation scorecards are research evidence only. Promotion requires independent future/out-of-sample "
            "outcomes and the existing Strategy Lab/human approval gates."
        ),
        "live_orders_submitted": 0,
    }


def _write_latest(app, report: dict) -> Path:
    root = Path(app.data_dir) / "strategy_shadow"
    root.mkdir(parents=True, exist_ok=True)
    latest = root / "latest_shadow_cycle.json"
    tmp = latest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, latest)
    return latest


def run_shadow_cycle(app, *, now: int | None = None, evm_max_rows: int = 250, solana_max_rows: int = 40) -> dict:
    now = int(now or time.time())
    envelopes = load_market_features(
        app,
        now=now,
        evm_max_rows=evm_max_rows,
        solana_max_rows=solana_max_rows,
    )
    evaluation = evaluate_envelopes(app, envelopes, now=now)
    report = {
        "generated_at": now,
        "feature_observations": len(envelopes),
        "feature_observations_by_chain": dict(
            (slug, sum(1 for env in envelopes if env.features.chain_slug == slug))
            for slug in sorted({env.features.chain_slug for env in envelopes})
        ),
        "evaluation": evaluation,
        "scorecard": scorecard(app, since=now - 3600, now=now),
        "safety": {
            "signing": False,
            "transaction_submission": False,
            "capital_changes": False,
            "live_auto_promotion": False,
            "quote_simulation_is_not_realised_pnl": True,
        },
    }
    report["latest_report_path"] = str(_write_latest(app, report))
    return report
