from __future__ import annotations

import csv
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path

from .config import load_kv_scoped

BEHAVIOUR_LABELS = {
    "TRIANGULAR_MULTI_HOP_ARBITRAGE": "Triangular / multi-hop arbitrage",
    "TWO_ASSET_ARBITRAGE": "Two-asset arbitrage",
    "STABLECOIN_ARBITRAGE": "Stablecoin arbitrage",
    "LIQUIDATION_CANDIDATE": "Liquidation candidate",
    "LIQUIDITY_MANAGEMENT_CANDIDATE": "Liquidity / LP management candidate",
    "MARKET_MAKING_CANDIDATE": "Market-making candidate",
    "MOMENTUM_SWING_CANDIDATE": "Momentum / swing candidate",
    "BRIDGE_CROSS_CHAIN_CANDIDATE": "Bridge / cross-chain candidate",
    "PRIVATE_ROUTED_ARBITRAGE": "Private-routed arbitrage",
    "AUTOMATED_EXECUTOR": "Automated executor",
    "TREASURY_TRANSFER": "Treasury / transfer",
    "UNKNOWN": "Unknown / insufficient evidence",
}

PROFIT_ELIGIBLE_DEFAULT = {
    "TRIANGULAR_MULTI_HOP_ARBITRAGE",
    "TWO_ASSET_ARBITRAGE",
    "STABLECOIN_ARBITRAGE",
    "LIQUIDATION_CANDIDATE",
    "LIQUIDITY_MANAGEMENT_CANDIDATE",
    "MARKET_MAKING_CANDIDATE",
    "PRIVATE_ROUTED_ARBITRAGE",
}

def label(name):
    return BEHAVIOUR_LABELS.get(name, name.replace("_", " ").title())

def _rows(path):
    if not Path(path).exists():
        return []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}

def _settings(settings):
    return load_kv_scoped(settings.csv_dir / "behaviour_settings.csv", settings.chain_id)

def _stable_symbols(settings):
    cfg = _settings(settings)
    raw = cfg.get("stable_symbols", "USDC|USDT|DAI|FDUSD|TUSD|USDE|USDS")
    return {x.strip().upper() for x in raw.split("|") if x.strip()}

def _registry_rules(settings):
    rules = []
    for row in _rows(settings.csv_dir / "behaviour_registry.csv"):
        if not _bool(row.get("enabled"), False):
            continue
        scope = (row.get("chain_id") or "*").strip()
        if scope not in {"", "*", "0", str(settings.chain_id)}:
            continue
        behaviour = (row.get("behaviour") or "").strip().upper()
        if not behaviour:
            continue
        address = (row.get("address") or "").strip().lower()
        selector = (row.get("selector") or "").strip().lower()
        try:
            confidence = float(row.get("confidence") or 90)
        except Exception:
            confidence = 90.0
        rules.append({
            "behaviour": behaviour,
            "address": address,
            "selector": selector,
            "confidence": confidence,
            "description": (row.get("description") or "").strip(),
        })
    return rules

def _route_symbols(conn, route_fingerprint):
    parts = (route_fingerprint or "").split("|")
    if len(parts) < 3:
        return []
    tokens = [x for x in parts[2].split(">") if x.startswith("0x")]
    out = []
    for token in tokens:
        row = conn.execute(
            "SELECT symbol FROM token_meta WHERE token=?", (token.lower(),)
        ).fetchone()
        out.append((row["symbol"] if row and row["symbol"] else token[:10]).upper())
    return out

def _explicit_rule(settings, target, selector):
    target = (target or "").lower()
    selector = (selector or "").lower()
    best = None
    for rule in _registry_rules(settings):
        if rule["address"] and rule["address"] != target:
            continue
        if rule["selector"] and rule["selector"] != selector:
            continue
        if not rule["address"] and not rule["selector"]:
            continue
        if best is None or rule["confidence"] > best["confidence"]:
            best = rule
    return best

def classify_evidence(conn, settings, row):
    explicit = _explicit_rule(
        settings,
        row["executor"] or row["to_addr"],
        row["selector"],
    )
    if explicit:
        return explicit["behaviour"], explicit["confidence"], (
            "Matched CSVbot/behaviour_registry.csv"
            + (": " + explicit["description"] if explicit["description"] else "")
        )

    classification = row["classification"] or ""
    proof = row["proof_quality"] or ""
    symbols = _route_symbols(conn, row["route_fingerprint"])
    stable = _stable_symbols(settings)
    non_base = [
        s for s in symbols
        if s != settings.wrapped_base_symbol.upper()
    ]
    stable_non_base = [s for s in non_base if s in stable]

    if classification == "TRIANGULAR_OR_MULTI_HOP_ARBITRAGE_CANDIDATE":
        if non_base and len(stable_non_base) == len(non_base):
            return "STABLECOIN_ARBITRAGE", 90.0, "Closed-cycle wrapped-base route using stable assets"
        if float(row["builder_payment_bnb"] or 0) > 0:
            return "PRIVATE_ROUTED_ARBITRAGE", 88.0, "Closed-cycle arbitrage with recognised builder payment"
        return "TRIANGULAR_MULTI_HOP_ARBITRAGE", 90.0, "Closed-cycle wrapped-base route with three or more token legs"

    if classification == "TWO_ASSET_ARBITRAGE_CANDIDATE":
        if non_base and len(stable_non_base) == len(non_base):
            return "STABLECOIN_ARBITRAGE", 88.0, "Closed-cycle wrapped-base/stable route"
        if float(row["builder_payment_bnb"] or 0) > 0:
            return "PRIVATE_ROUTED_ARBITRAGE", 86.0, "Two-asset closed cycle with recognised builder payment"
        return "TWO_ASSET_ARBITRAGE", 88.0, "Closed-cycle wrapped-base route with two assets"

    if classification in {"TOKEN_BUY_OR_ENTRY_CANDIDATE", "TOKEN_SALE_OR_EXIT_CANDIDATE"}:
        return "MOMENTUM_SWING_CANDIDATE", 45.0, (
            "Entry/exit-like flow detected; full position P&L requires matching later transactions"
        )

    if classification == "TRANSFER_OR_TREASURY_PATTERN":
        return "TREASURY_TRANSFER", 80.0, "Transfer/collection pattern; excluded from profit leaderboard"

    if classification == "AUTOMATED_EXECUTOR_PATTERN":
        return "AUTOMATED_EXECUTOR", 60.0, "Automation detected but trade type is not yet proved"

    if proof == "NO_POSITIVE_DELTA":
        return "UNKNOWN", 30.0, "Insufficient token-flow evidence"

    return "UNKNOWN", 35.0, "No high-confidence behaviour rule matched"

def _rank(values, reverse=True):
    # Dense-ish deterministic ranking: same value can share the same rank.
    ordered = sorted(set(values), reverse=reverse)
    return {v: i + 1 for i, v in enumerate(ordered)}

def _score_norm(v, max_v):
    if max_v <= 0:
        return 0.0
    return max(0.0, min(100.0, (max(0.0, v) / max_v) * 100.0))

def _median_interval(timestamps):
    ts = sorted(int(x) for x in timestamps if x is not None)
    if len(ts) < 2:
        return None
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b >= a]
    return float(statistics.median(gaps)) if gaps else None

def _aggregate(items, min_active_minutes):
    evidence_count = len(items)
    wallets = {x["wallet"] for x in items}
    proven = [x for x in items if x["proof_quality"] == "PROVEN_WRAPPED_BASE" and x["profit_base"] is not None]
    positive = [x for x in proven if float(x["profit_base"]) > 0]
    negative = [x for x in proven if float(x["profit_base"]) < 0]

    total_base = sum(float(x["profit_base"] or 0) for x in proven)
    usd_values = [x["profit_usd"] for x in proven if x["profit_usd"] is not None]
    total_usd = sum(float(x) for x in usd_values) if usd_values else None
    avg_base = total_base / len(proven) if proven else None
    median_pos = (
        statistics.median(float(x["profit_base"]) for x in positive)
        if positive else None
    )

    timestamps = [int(x["block_timestamp"]) for x in proven if x["block_timestamp"] is not None]
    if timestamps:
        span_seconds = max(timestamps) - min(timestamps)
    else:
        span_seconds = 0
    active_seconds = max(float(min_active_minutes) * 60.0, float(span_seconds or 0))
    active_hours = active_seconds / 3600.0 if active_seconds > 0 else 1.0
    pph_base = total_base / active_hours
    pph_usd = (total_usd / active_hours) if total_usd is not None else None
    ratio = (len(positive) / len(proven)) if proven else 0.0
    interval = _median_interval([x["block_timestamp"] for x in positive])

    return {
        "evidence_count": evidence_count,
        "wallet_count": len(wallets),
        "proven_count": len(proven),
        "positive_count": len(positive),
        "negative_count": len(negative),
        "total_net_base": total_base,
        "total_net_usd": total_usd,
        "avg_net_base": avg_base,
        "median_positive_net_base": median_pos,
        "active_hours": active_hours,
        "profit_per_hour_base": pph_base,
        "profit_per_hour_usd": pph_usd,
        "median_seconds_between_positive": interval,
        "positive_ratio": ratio,
    }

def refresh_behaviour_research(conn, settings):
    now = int(time.time())
    rows = conn.execute(
        """SELECT p.*, t.to_addr, t.selector, b.timestamp AS block_timestamp
           FROM profit_evidence p
           JOIN transactions t ON t.tx_hash=p.tx_hash
           LEFT JOIN blocks b ON b.number=t.block_number"""
    ).fetchall()

    conn.execute("DELETE FROM trade_behaviour_evidence")
    evidence = []
    for row in rows:
        behaviour, confidence, notes = classify_evidence(conn, settings, row)
        item = {
            "tx_hash": row["tx_hash"],
            "wallet": row["wallet"],
            "behaviour": behaviour,
            "confidence": confidence,
            "profit_base": row["net_base"] if row["proof_quality"] == "PROVEN_WRAPPED_BASE" else None,
            "profit_usd": row["net_usd"] if row["proof_quality"] == "PROVEN_WRAPPED_BASE" else None,
            "proof_quality": row["proof_quality"],
            "block_timestamp": row["block_timestamp"],
            "executor": row["executor"],
            "selector": row["selector"],
            "notes": notes,
        }
        evidence.append(item)
        conn.execute(
            """INSERT INTO trade_behaviour_evidence(
                 tx_hash,wallet,behaviour,behaviour_confidence,profit_base,
                 profit_usd,proof_quality,block_timestamp,executor,selector,
                 notes,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item["tx_hash"], item["wallet"], item["behaviour"], item["confidence"],
                item["profit_base"], item["profit_usd"], item["proof_quality"],
                item["block_timestamp"], item["executor"], item["selector"],
                item["notes"], now,
            ),
        )

    cfg = _settings(settings)
    min_active_minutes = float(cfg.get("ranking_min_active_minutes", "60"))
    full_score_evidence = max(1.0, float(cfg.get("ranking_evidence_full_score", "50")))
    w_profit = float(cfg.get("ranking_weight_total_profit", "40"))
    w_speed = float(cfg.get("ranking_weight_profit_speed", "30"))
    w_consistency = float(cfg.get("ranking_weight_consistency", "20"))
    w_evidence = float(cfg.get("ranking_weight_evidence", "10"))
    weight_total = max(1.0, w_profit + w_speed + w_consistency + w_evidence)

    grouped = defaultdict(list)
    wallet_grouped = defaultdict(list)
    for item in evidence:
        grouped[item["behaviour"]].append(item)
        wallet_grouped[(item["wallet"], item["behaviour"])].append(item)

    raw = {}
    for behaviour, items in grouped.items():
        raw[behaviour] = _aggregate(items, min_active_minutes)

    max_profit = max([max(0.0, x["total_net_base"]) for x in raw.values()] or [0.0])
    max_speed = max([max(0.0, x["profit_per_hour_base"]) for x in raw.values()] or [0.0])

    for behaviour, a in raw.items():
        a["profit_score"] = _score_norm(a["total_net_base"], max_profit)
        a["speed_score"] = _score_norm(a["profit_per_hour_base"], max_speed)
        a["consistency_score"] = a["positive_ratio"] * 100.0
        a["evidence_score"] = min(100.0, (a["proven_count"] / full_score_evidence) * 100.0)
        a["overall_score"] = (
            a["profit_score"] * w_profit
            + a["speed_score"] * w_speed
            + a["consistency_score"] * w_consistency
            + a["evidence_score"] * w_evidence
        ) / weight_total

    profit_rank = _rank([round(x["total_net_base"], 12) for x in raw.values()])
    speed_rank = _rank([round(x["profit_per_hour_base"], 12) for x in raw.values()])
    overall_rank = _rank([round(x["overall_score"], 8) for x in raw.values()])

    conn.execute("DELETE FROM behaviour_rankings")
    for behaviour, a in raw.items():
        conn.execute(
            """INSERT INTO behaviour_rankings(
                 behaviour,evidence_count,wallet_count,proven_count,positive_count,
                 negative_count,total_net_base,total_net_usd,avg_net_base,
                 median_positive_net_base,active_hours,profit_per_hour_base,
                 profit_per_hour_usd,median_seconds_between_positive,positive_ratio,
                 profit_score,speed_score,consistency_score,evidence_score,
                 overall_score,rank_profit,rank_speed,rank_overall,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                behaviour, a["evidence_count"], a["wallet_count"], a["proven_count"],
                a["positive_count"], a["negative_count"], a["total_net_base"],
                a["total_net_usd"], a["avg_net_base"], a["median_positive_net_base"],
                a["active_hours"], a["profit_per_hour_base"], a["profit_per_hour_usd"],
                a["median_seconds_between_positive"], a["positive_ratio"],
                a["profit_score"], a["speed_score"], a["consistency_score"],
                a["evidence_score"], a["overall_score"],
                profit_rank[round(a["total_net_base"], 12)],
                speed_rank[round(a["profit_per_hour_base"], 12)],
                overall_rank[round(a["overall_score"], 8)],
                now,
            ),
        )

    conn.execute("DELETE FROM wallet_behaviour_rankings")
    for (wallet, behaviour), items in wallet_grouped.items():
        a = _aggregate(items, min_active_minutes)
        # wallet score is intentionally local and simple; behaviour leaderboard
        # is the primary comparison surface.
        evidence_score = min(100.0, (a["proven_count"] / full_score_evidence) * 100.0)
        consistency = a["positive_ratio"] * 100.0
        overall = (
            max(0.0, a["profit_per_hour_base"]) * 20.0
            + max(0.0, a["total_net_base"]) * 20.0
            + consistency * 0.4
            + evidence_score * 0.2
        )
        conn.execute(
            """INSERT INTO wallet_behaviour_rankings(
                 wallet,behaviour,evidence_count,proven_count,positive_count,
                 negative_count,total_net_base,total_net_usd,avg_net_base,
                 active_hours,profit_per_hour_base,profit_per_hour_usd,
                 median_seconds_between_positive,positive_ratio,overall_score,
                 updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                wallet, behaviour, a["evidence_count"], a["proven_count"],
                a["positive_count"], a["negative_count"], a["total_net_base"],
                a["total_net_usd"], a["avg_net_base"], a["active_hours"],
                a["profit_per_hour_base"], a["profit_per_hour_usd"],
                a["median_seconds_between_positive"], a["positive_ratio"],
                overall, now,
            ),
        )

    conn.commit()
    return {
        "evidence_rows": len(evidence),
        "behaviours": len(raw),
        "wallet_behaviour_rows": len(wallet_grouped),
    }

def best_behaviours(conn, limit=10):
    return conn.execute(
        """SELECT * FROM behaviour_rankings
           WHERE proven_count>0
           ORDER BY rank_overall, rank_profit, rank_speed
           LIMIT ?""",
        (limit,),
    ).fetchall()

def fastest_behaviours(conn, limit=10):
    return conn.execute(
        """SELECT * FROM behaviour_rankings
           WHERE proven_count>0
           ORDER BY profit_per_hour_base DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()

def most_profitable_behaviours(conn, limit=10):
    return conn.execute(
        """SELECT * FROM behaviour_rankings
           WHERE proven_count>0
           ORDER BY total_net_base DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
