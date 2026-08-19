from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .strategy_lab import connect, register_strategy

# Public research sources are advisory inputs for the Strategy Laboratory.  The bot must
# never execute third-party repository code or trust a public wallet simply because it
# appears profitable.  Every derived idea remains SHADOW until independently validated.
PUBLIC_RESEARCH_TOOLS = [
    {
        "tool": "Dune",
        "category": "ONCHAIN_WALLET_AND_DEX_RESEARCH",
        "chains": ["EVM", "SOLANA"],
        "url": "https://docs.dune.com/api-reference/overview/getting-started",
        "use": "Query public DEX trades, wallet activity, route behaviour and cohort performance; reconstruct realised outcomes rather than copying raw win counts.",
        "credential": "DUNE_API_KEY optional for automated API use",
        "safe_mode": "READ_ONLY_RESEARCH",
    },
    {
        "tool": "DEX Screener API",
        "category": "POOL_TOKEN_MARKET_DATA",
        "chains": ["EVM", "SOLANA"],
        "url": "https://docs.dexscreener.com/api/reference",
        "use": "Discover/search token pairs and compare liquidity, volume, transaction flow, price change and pool age before a shadow signal is considered.",
        "credential": "Public API subject to rate limits/terms",
        "safe_mode": "READ_ONLY_RESEARCH",
    },
    {
        "tool": "Etherscan API V2",
        "category": "EVM_WALLET_TRANSACTION_RESEARCH",
        "chains": ["EVM"],
        "url": "https://docs.etherscan.io/api-reference/endpoint/txlist",
        "use": "Fetch EVM address transaction histories for candidate-wallet reconstruction and route/contract interaction classification.",
        "credential": "ETHERSCAN_API_KEY",
        "safe_mode": "READ_ONLY_RESEARCH",
    },
    {
        "tool": "GitHub public code search",
        "category": "PUBLIC_BOT_ARCHITECTURE_RESEARCH",
        "chains": ["EVM", "SOLANA"],
        "url": "https://docs.github.com/en/rest/search/search",
        "use": "Study public trading-bot architecture, indicators, simulators and risk controls. Extract ideas only; never execute or vendor untrusted repository code automatically.",
        "credential": "Optional GitHub token for higher API limits",
        "safe_mode": "READ_ONLY_IDEA_EXTRACTION",
    },
    {
        "tool": "DefiLlama",
        "category": "CHAIN_PROTOCOL_ACTIVITY",
        "chains": ["EVM", "SOLANA"],
        "url": "https://defillama.com/docs/api",
        "use": "Compare chain/protocol TVL, DEX volume, fees and activity regimes so strategy tests can be conditioned on market environment.",
        "credential": "Public datasets; Pro API optional",
        "safe_mode": "READ_ONLY_RESEARCH",
    },
    {
        "tool": "Jupiter",
        "category": "SOLANA_ROUTE_AND_EXECUTION_RESEARCH",
        "chains": ["SOLANA"],
        "url": "https://dev.jup.ag/",
        "use": "Use quote/route information to evaluate executable Solana edge, route quality and expected costs in simulation/shadow mode.",
        "credential": "Follow current Jupiter API requirements",
        "safe_mode": "QUOTE_AND_SIMULATION_ONLY_FOR_LAB",
    },
]

FORECAST_RESEARCH_SPEC = {
    "objective": "Forecast whether an eligible opportunity has positive realised net edge after fees, slippage, price impact and latency reserve; not merely whether price rises.",
    "chain_scope": ["SOLANA", "EVM"],
    "feature_groups": [
        "price_return_and_acceleration",
        "buy_sell_flow_and_flow_acceleration",
        "liquidity_depth_and_change",
        "spread_price_impact_and_quote_age",
        "wallet_cohort_flow_without_single_wallet_dependency",
        "route_quality_and_execution_failures",
        "gas_priority_fee_and_builder_cost",
        "volatility_and_market_regime",
        "pool_age_holder_or_flow_concentration_when_available",
    ],
    "required_validation": [
        "strict_time_ordered_train_validation_test_split",
        "no_future_or_post_trade_features",
        "net_pnl_after_recorded_costs",
        "profit_factor_and_largest_loss",
        "probability_calibration_brier_or_log_loss",
        "precision_recall_at_trade_threshold",
        "regime_and_chain_specific_breakdown",
        "shadow_before_canary",
    ],
    "output": "probability_positive_net_edge plus expected_net_edge and uncertainty; abstain when evidence is weak",
}


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _anon(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value:
        return "unknown"
    return "wallet_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)


def _chain_db_files(app) -> list[Path]:
    root = Path(app.data_dir)
    return sorted(
        p for p in root.glob("*.sqlite3")
        if p.name not in {"strategy_lab.sqlite3"} and p.is_file()
    )


def profitable_wallet_research(app, *, limit_per_chain: int = 12) -> dict:
    """Summarise profitable public-wallet evidence already learned by the bot.

    Wallet identities are hashed in the sanitised report.  Profit alone is not treated as
    proof of a replicable strategy; the AI reviewer must corroborate route/behaviour data,
    sample size, costs and concentration before proposing a shadow experiment.
    """
    chains = []
    for path in _chain_db_files(app):
        try:
            conn = sqlite3.connect(path, timeout=3)
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "profit_evidence"):
                conn.close()
                continue
            rows = conn.execute(
                """SELECT wallet,
                          COUNT(*) AS proven_count,
                          SUM(CASE WHEN net_base > 0 THEN net_base ELSE 0 END) AS gross_positive,
                          SUM(CASE WHEN net_base < 0 THEN -net_base ELSE 0 END) AS gross_negative,
                          SUM(COALESCE(net_base,0)) AS net,
                          COUNT(DISTINCT COALESCE(route_fingerprint,'')) AS route_count
                   FROM profit_evidence
                   WHERE proof_quality='PROVEN_WRAPPED_BASE'
                   GROUP BY wallet
                   HAVING COUNT(*) >= 2 AND SUM(COALESCE(net_base,0)) > 0
                   ORDER BY net DESC
                   LIMIT ?""",
                (max(1, min(100, int(limit_per_chain))),),
            ).fetchall()
            wallets = []
            for row in rows:
                gp = float(row["gross_positive"] or 0)
                gl = float(row["gross_negative"] or 0)
                pf = gp / gl if gl > 0 else (99.0 if gp > 0 else 0.0)
                wallets.append({
                    "wallet_ref": _anon(row["wallet"]),
                    "proven_count": int(row["proven_count"] or 0),
                    "net_base": float(row["net"] or 0),
                    "gross_positive_base": gp,
                    "gross_negative_base": gl,
                    "profit_factor": round(pf, 6),
                    "distinct_route_count": int(row["route_count"] or 0),
                    "research_only": True,
                })
            patterns = []
            if _table_exists(conn, "strategy_patterns"):
                for row in conn.execute(
                    """SELECT pattern_id,strategy_class,tx_count,wallet_count,proven_profit_count,
                              avg_net_base,confidence,replicability,status
                       FROM strategy_patterns
                       WHERE COALESCE(proven_profit_count,0) > 0
                       ORDER BY COALESCE(avg_net_base,0) DESC, COALESCE(confidence,0) DESC
                       LIMIT 30"""
                ).fetchall():
                    patterns.append({
                        "pattern_ref": str(row["pattern_id"] or "")[:80],
                        "strategy_class": row["strategy_class"],
                        "tx_count": int(row["tx_count"] or 0),
                        "wallet_count": int(row["wallet_count"] or 0),
                        "proven_profit_count": int(row["proven_profit_count"] or 0),
                        "avg_net_base": float(row["avg_net_base"] or 0),
                        "confidence": float(row["confidence"] or 0),
                        "replicability": float(row["replicability"] or 0),
                        "status": row["status"],
                    })
            conn.close()
            chains.append({"chain_slug": path.stem, "profitable_wallet_cohorts": wallets, "learned_patterns": patterns})
        except Exception as exc:
            chains.append({"chain_slug": path.stem, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "available": True,
        "chains": chains,
        "instruction": (
            "Use profitable wallets as research evidence, not as automatic copy targets. Look for repeated route/behaviour "
            "structures shared by multiple profitable wallets, reconstruct realised net after costs, test robustness across "
            "time and regimes, and convert only replicable findings into SHADOW Strategy Lab hypotheses."
        ),
    }


def learned_pattern_portability(app) -> dict:
    by_class: dict[str, list[dict]] = defaultdict(list)
    for path in _chain_db_files(app):
        try:
            conn = sqlite3.connect(path, timeout=3)
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "strategy_patterns"):
                conn.close()
                continue
            for row in conn.execute(
                """SELECT strategy_class,tx_count,wallet_count,proven_profit_count,avg_net_base,confidence,replicability
                   FROM strategy_patterns
                   WHERE COALESCE(proven_profit_count,0)>0 AND COALESCE(replicability,0)>0
                   ORDER BY COALESCE(replicability,0) DESC LIMIT 100"""
            ).fetchall():
                key = str(row["strategy_class"] or "UNKNOWN").upper()
                by_class[key].append({
                    "chain_slug": path.stem,
                    "tx_count": int(row["tx_count"] or 0),
                    "wallet_count": int(row["wallet_count"] or 0),
                    "proven_profit_count": int(row["proven_profit_count"] or 0),
                    "avg_net_base": float(row["avg_net_base"] or 0),
                    "confidence": float(row["confidence"] or 0),
                    "replicability": float(row["replicability"] or 0),
                })
            conn.close()
        except Exception:
            continue
    candidates = []
    for strategy_class, observations in sorted(by_class.items()):
        candidates.append({
            "strategy_class": strategy_class,
            "observed_chains": sorted({x["chain_slug"] for x in observations}),
            "observations": observations[:20],
            "portability_action": "SHADOW_TEST_ON_MISSING_CHAIN_TYPES",
            "requirements": [
                "same economic hypothesis, chain-specific costs",
                "re-quote current executable route",
                "chain-specific liquidity/sellability validation",
                "separate Solana priority/Jito and EVM gas/MEV assumptions",
                "no promotion from another chain's results alone",
            ],
        })
    return {"chain_scope": ["SOLANA", "EVM"], "pattern_candidates": candidates[:50]}


def _asset_request_path(app) -> Path:
    return Path(app.data_dir) / "strategy_lab_asset_requests.json"


def request_asset(
    app,
    *,
    chain: str,
    asset: str,
    symbol: str = "",
    reason: str,
    evidence: str = "",
    proposed_by: str = "strategy_lab",
) -> dict:
    """Queue a missing-asset request; never modifies tokens.csv or auto-trade settings."""
    chain = str(chain or "").upper().strip()
    asset = str(asset or "").strip()
    symbol = str(symbol or "").upper().strip()
    reason = str(reason or "").strip()
    if chain not in {"SOLANA", "EVM"}:
        raise ValueError("asset request chain must be SOLANA or EVM")
    if not asset or not reason:
        raise ValueError("asset and reason are required")
    key = hashlib.sha256(f"{chain}|{asset.lower()}".encode("utf-8")).hexdigest()[:16]
    path = _asset_request_path(app)
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        current = []
    if not isinstance(current, list):
        current = []
    now = int(time.time())
    item = {
        "request_id": "asset_" + key,
        "chain": chain,
        "asset": asset,
        "symbol": symbol,
        "reason": reason[:1200],
        "evidence": str(evidence or "")[:2000],
        "proposed_by": str(proposed_by or "strategy_lab")[:120],
        "status": "REQUESTED_REVIEW",
        "requested_at": now,
        "auto_added": False,
        "required_before_enable": [
            "identity_and_metadata_validation",
            "liquidity_and_sellability_validation",
            "quote_and_simulation_pass",
            "cost_and_slippage_model",
            "risk_classification",
        ],
    }
    replaced = False
    for i, row in enumerate(current):
        if isinstance(row, dict) and row.get("request_id") == item["request_id"]:
            item["requested_at"] = int(row.get("requested_at") or now)
            current[i] = item
            replaced = True
            break
    if not replaced:
        current.append(item)
    current = current[-500:]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return item


def asset_request_report(app) -> dict:
    path = _asset_request_path(app)
    try:
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        rows = []
    if not isinstance(rows, list):
        rows = []
    return {
        "pending": [r for r in rows if isinstance(r, dict) and r.get("status") == "REQUESTED_REVIEW"][-100:],
        "policy": "AI/learner may request missing assets, but cannot edit the configured/live product universe automatically.",
    }


def ensure_cross_chain_scope(app) -> dict:
    """Mark built-in hypotheses as portable research across Solana and EVM.

    This changes only Strategy Lab metadata.  It does not connect a strategy to a LIVE
    executor or infer that performance on one chain transfers to another.
    """
    updated = []
    with connect(app) as conn:
        rows = conn.execute("SELECT strategy_id,name,params_json FROM strategy_lab_registry").fetchall()
        now = int(time.time())
        for row in rows:
            try:
                params = json.loads(row["params_json"] or "{}")
            except Exception:
                params = {}
            if params.get("chain_scope") == ["SOLANA", "EVM"] and params.get("portable_signal") is True:
                continue
            params["chain_scope"] = ["SOLANA", "EVM"]
            params["portable_signal"] = True
            params["chain_specific_cost_model_required"] = True
            params["cross_chain_live_inference_forbidden"] = True
            conn.execute(
                "UPDATE strategy_lab_registry SET params_json=?, updated_at=? WHERE strategy_id=?",
                (json.dumps(params, sort_keys=True, separators=(",", ":")), now, row["strategy_id"]),
            )
            updated.append({"strategy_id": row["strategy_id"], "name": row["name"]})
        conn.commit()

    # Add two research families that explicitly push the learner beyond leader copying.
    seeded = []
    for idea in (
        {
            "name": "Profitable Wallet Pattern Transfer",
            "family": "LEARNED_PATTERN",
            "hypothesis": "Repeated behaviours shared by multiple independently profitable public wallets can become chain-neutral shadow hypotheses when net outcomes, costs and concentration controls remain robust.",
            "params": {
                "chain_scope": ["SOLANA", "EVM"],
                "research_profitable_wallet_cohorts": True,
                "require_multiple_wallets": True,
                "require_proven_net": True,
                "copy_single_wallet": False,
            },
        },
        {
            "name": "Forecasted Positive Net Edge",
            "family": "PREDICTIVE",
            "hypothesis": "A calibrated cross-chain forecast of positive net edge can improve opportunity selection when trained without lookahead and evaluated after real execution costs.",
            "params": {
                "chain_scope": ["SOLANA", "EVM"],
                "forecast_target": "positive_net_edge_after_costs",
                "abstain_on_low_confidence": True,
                "shadow_only": True,
            },
        },
    ):
        try:
            seeded.append(register_strategy(
                app,
                name=idea["name"],
                family=idea["family"],
                source="LEARNED_PATTERN" if idea["family"] == "LEARNED_PATTERN" else "MARKET_NATIVE",
                hypothesis=idea["hypothesis"],
                params=idea["params"],
                proposed_by="cross_chain_strategy_lab",
            ))
        except Exception:
            pass
    return {"metadata_updated": updated, "research_strategies_seeded": seeded}


def build_research_report(app) -> dict:
    return {
        "public_research_tools": PUBLIC_RESEARCH_TOOLS,
        "profitable_wallet_research": profitable_wallet_research(app),
        "cross_chain_pattern_portability": learned_pattern_portability(app),
        "forecast_research": FORECAST_RESEARCH_SPEC,
        "asset_requests": asset_request_report(app),
        "research_rules": [
            "Research public wallets/bots and public code for ideas; do not execute untrusted third-party code.",
            "Prefer behaviours repeated across multiple profitable wallets over single-wallet imitation.",
            "Use realised net P&L after costs, profit factor, loss magnitude and out-of-sample evidence.",
            "Every new strategy and every cross-chain port starts SHADOW.",
            "Solana and EVM may use the same economic strategy family, but each chain keeps its own cost/liquidity/execution model.",
            "If a candidate asset is absent, issue an asset request; never silently auto-enable it for LIVE trading.",
            "Forecast trade quality as probability/expected value of positive net edge, with an abstain option when uncertainty is high.",
        ],
    }
