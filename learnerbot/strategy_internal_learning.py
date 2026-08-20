from __future__ import annotations

"""First-party Strategy Lab evidence from the bot's own learning systems.

INT1 is the Learning Bot's proved outcomes and learned strategy patterns already
present in strategy_lab_research. INT2 is SiBot's observed-wallet behaviour,
candidate ranking and copy-recommendation evidence. Both are research-only inputs:
wallet identities are anonymised and no row in this module authorises LIVE trading.
"""

import hashlib
import sqlite3
from pathlib import Path
from typing import Any


INTERNAL_STRATEGY_SOURCES = [
    {
        "source_id": "INT1",
        "tool": "Learning Bot Internal Evidence",
        "source_class": "FIRST_PARTY_LEARNING_EVIDENCE",
        "url": "internal://learning-bot",
        "chains": ["EVM", "SOLANA"],
        "use": (
            "Use the bot's own proven profit_evidence and learned strategy_patterns to identify repeated, "
            "replicable behaviours and cross-chain hypotheses. This is first-party measured evidence, not an "
            "external idea feed."
        ),
        "safe_mode": "READ_ONLY_INTERNAL_RESEARCH",
        "report_paths": ["profitable_wallet_research", "cross_chain_pattern_portability"],
        "priority": 1,
    },
    {
        "source_id": "INT2",
        "tool": "SiBot Observed-Wallet Learning",
        "source_class": "FIRST_PARTY_WALLET_BEHAVIOUR",
        "url": "internal://sibot-wallet-learning",
        "chains": ["EVM", "SOLANA"],
        "use": (
            "Study behaviours repeatedly observed across public wallets using SiBot's wallet/behaviour scores, "
            "copy-candidate evidence and recommendations. Learn the behaviour; never treat one wallet as proof "
            "or blindly copy a wallet."
        ),
        "safe_mode": "READ_ONLY_INTERNAL_RESEARCH",
        "report_paths": ["sibot_observed_wallet_learning"],
        "priority": 2,
    },
]


def _anon(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "unknown"
    return "wallet_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _db_files(app) -> list[Path]:
    root = Path(app.data_dir)
    return sorted(p for p in root.glob("*.sqlite3") if p.is_file())


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def sibot_observed_wallet_learning(app, *, limit_per_chain: int = 20) -> dict:
    """Return compact anonymised evidence showing what SiBot has learned from others."""
    limit = max(1, min(100, int(limit_per_chain)))
    chains: list[dict] = []

    for path in _db_files(app):
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=3)
            conn.row_factory = sqlite3.Row
            if not any(
                _table_exists(conn, name)
                for name in (
                    "behaviour_rankings",
                    "wallet_behaviour_rankings",
                    "copy_wallet_candidates",
                    "copy_trade_recommendations",
                )
            ):
                continue

            behaviour_rankings = []
            if _table_exists(conn, "behaviour_rankings"):
                rows = conn.execute(
                    """SELECT behaviour,evidence_count,wallet_count,proven_count,positive_count,negative_count,
                              total_net_base,profit_per_hour_base,positive_ratio,overall_score,rank_overall
                       FROM behaviour_rankings
                       ORDER BY COALESCE(overall_score,0) DESC, COALESCE(total_net_base,0) DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
                for row in rows:
                    behaviour_rankings.append({
                        "behaviour": row["behaviour"],
                        "evidence_count": _i(row["evidence_count"]),
                        "wallet_count": _i(row["wallet_count"]),
                        "proven_count": _i(row["proven_count"]),
                        "positive_count": _i(row["positive_count"]),
                        "negative_count": _i(row["negative_count"]),
                        "total_net_base": _f(row["total_net_base"]),
                        "profit_per_hour_base": _f(row["profit_per_hour_base"]),
                        "positive_ratio": _f(row["positive_ratio"]),
                        "overall_score": _f(row["overall_score"]),
                        "rank_overall": _i(row["rank_overall"]),
                    })

            wallet_behaviours = []
            if _table_exists(conn, "wallet_behaviour_rankings"):
                rows = conn.execute(
                    """SELECT wallet,behaviour,evidence_count,proven_count,positive_count,negative_count,
                              total_net_base,profit_per_hour_base,positive_ratio,overall_score
                       FROM wallet_behaviour_rankings
                       ORDER BY COALESCE(total_net_base,0) DESC, COALESCE(overall_score,0) DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
                for row in rows:
                    wallet_behaviours.append({
                        "wallet_ref": _anon(row["wallet"]),
                        "behaviour": row["behaviour"],
                        "evidence_count": _i(row["evidence_count"]),
                        "proven_count": _i(row["proven_count"]),
                        "positive_count": _i(row["positive_count"]),
                        "negative_count": _i(row["negative_count"]),
                        "total_net_base": _f(row["total_net_base"]),
                        "profit_per_hour_base": _f(row["profit_per_hour_base"]),
                        "positive_ratio": _f(row["positive_ratio"]),
                        "overall_score": _f(row["overall_score"]),
                    })

            candidates = []
            if _table_exists(conn, "copy_wallet_candidates"):
                rows = conn.execute(
                    """SELECT wallet,behaviour,status,pass_checks,copy_score,bot_score,avg_behaviour_confidence,
                              evidence_count,proven_count,positive_count,negative_count,positive_ratio,total_net_base,
                              profit_per_hour_base,active_hours,avg_net_base,max_positive_base,max_loss_base,
                              median_seconds_between_positive,rejection_reasons
                       FROM copy_wallet_candidates
                       ORDER BY CASE WHEN UPPER(COALESCE(status,'')) IN ('PASS','ACCEPT','APPROVE','ELIGIBLE','COPY')
                                     THEN 0 ELSE 1 END,
                                COALESCE(copy_score,0) DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
                for row in rows:
                    candidates.append({
                        "wallet_ref": _anon(row["wallet"]),
                        "behaviour": row["behaviour"],
                        "status": row["status"],
                        "pass_checks": _i(row["pass_checks"]),
                        "copy_score": _f(row["copy_score"]),
                        "bot_score": _f(row["bot_score"]),
                        "avg_behaviour_confidence": _f(row["avg_behaviour_confidence"]),
                        "evidence_count": _i(row["evidence_count"]),
                        "proven_count": _i(row["proven_count"]),
                        "positive_count": _i(row["positive_count"]),
                        "negative_count": _i(row["negative_count"]),
                        "positive_ratio": _f(row["positive_ratio"]),
                        "total_net_base": _f(row["total_net_base"]),
                        "profit_per_hour_base": _f(row["profit_per_hour_base"]),
                        "active_hours": _f(row["active_hours"]),
                        "avg_net_base": _f(row["avg_net_base"]),
                        "max_positive_base": _f(row["max_positive_base"]),
                        "max_loss_base": _f(row["max_loss_base"]),
                        "median_seconds_between_positive": _f(row["median_seconds_between_positive"]),
                        "rejection_reasons": str(row["rejection_reasons"] or "")[:1000],
                    })

            recommendations = []
            if _table_exists(conn, "copy_trade_recommendations"):
                rows = conn.execute(
                    """SELECT wallet,behaviour,route_id,action,recommendation_mode,reason,
                              conservative_net_profit_base,signal_age_seconds,checks_passed,checks_failed,
                              observed_at,created_at
                       FROM copy_trade_recommendations
                       ORDER BY COALESCE(created_at,0) DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
                for row in rows:
                    recommendations.append({
                        "wallet_ref": _anon(row["wallet"]),
                        "behaviour": row["behaviour"],
                        "route_id": str(row["route_id"] or "")[:160],
                        "action": row["action"],
                        "recommendation_mode": row["recommendation_mode"],
                        "reason": str(row["reason"] or "")[:1000],
                        "conservative_net_profit_base": _f(row["conservative_net_profit_base"]),
                        "signal_age_seconds": _f(row["signal_age_seconds"]),
                        "checks_passed": _i(row["checks_passed"]),
                        "checks_failed": _i(row["checks_failed"]),
                        "observed_at": _i(row["observed_at"]),
                        "created_at": _i(row["created_at"]),
                    })

            chains.append({
                "chain_slug": path.stem,
                "behaviour_rankings": behaviour_rankings,
                "top_wallet_behaviours": wallet_behaviours,
                "copy_wallet_candidates": candidates,
                "recent_copy_recommendations": recommendations,
            })
        except Exception as exc:
            chains.append({"chain_slug": path.stem, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            if conn is not None:
                conn.close()

    return {
        "source_id": "INT2",
        "name": "SiBot Observed-Wallet Learning",
        "available": True,
        "research_only": True,
        "live_execution_authorised": False,
        "wallet_identity_exposed": False,
        "chains": chains,
        "learning_rule": (
            "Learn repeated profitable behaviour shared by multiple wallets. A wallet is evidence, not a strategy. "
            "Prefer behaviours with proven samples, positive realised net after costs, acceptable downside and "
            "replicability; convert the behaviour into an independent falsifiable SHADOW strategy rather than blindly "
            "following a single wallet."
        ),
    }


def internal_source_catalogue() -> list[dict]:
    return [dict(row) for row in INTERNAL_STRATEGY_SOURCES]


def attach_internal_learning_sources(report: dict, app) -> dict:
    report["first_party_research_sources"] = internal_source_catalogue()
    report["research_priority_order"] = [
        "INT1: Learning Bot proved outcomes and learned patterns",
        "INT2: SiBot observed-wallet behaviour and candidate evidence",
        "EXT1-EXT4: fresh external evidence",
        "CURATED_EXTERNAL: approved reference catalogue",
    ]
    report["sibot_observed_wallet_learning"] = sibot_observed_wallet_learning(app)
    report["internal_learning_instruction"] = (
        "Start strategy discovery with INT1 and INT2. Use the Learning Bot's own realised/proven evidence and SiBot's "
        "multi-wallet behaviour observations as primary first-party evidence. Then use external sources to corroborate, "
        "challenge or extend those findings. Never promote a strategy because one observed wallet was profitable."
    )
    return report
