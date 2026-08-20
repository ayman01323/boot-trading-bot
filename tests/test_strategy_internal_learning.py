from __future__ import annotations

import json
from types import SimpleNamespace

from learnerbot.db import connect
from learnerbot.strategy_internal_learning import (
    attach_internal_learning_sources,
    internal_source_catalogue,
    sibot_observed_wallet_learning,
)


def _app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path)


def _seed(tmp_path):
    db_path = tmp_path / "bsc.sqlite3"
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO behaviour_rankings(
                   behaviour,evidence_count,wallet_count,proven_count,positive_count,negative_count,
                   total_net_base,profit_per_hour_base,positive_ratio,overall_score,rank_overall,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("TRIANGULAR_MULTI_HOP_ARBITRAGE", 20, 5, 14, 14, 0, 1.5, 0.3, 1.0, 92.0, 1, 1),
        )
        conn.execute(
            """INSERT INTO wallet_behaviour_rankings(
                   wallet,behaviour,evidence_count,proven_count,positive_count,negative_count,total_net_base,
                   profit_per_hour_base,positive_ratio,overall_score,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("0x1111111111111111111111111111111111111111", "TRIANGULAR_MULTI_HOP_ARBITRAGE", 20, 14, 14, 0, 1.5, 0.3, 1.0, 90.0, 1),
        )
        conn.execute(
            """INSERT INTO copy_wallet_candidates(
                   wallet,behaviour,status,pass_checks,copy_score,bot_score,avg_behaviour_confidence,
                   evidence_count,proven_count,positive_count,negative_count,positive_ratio,total_net_base,
                   profit_per_hour_base,active_hours,avg_net_base,max_positive_base,max_loss_base,
                   median_seconds_between_positive,rejection_reasons,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("0x1111111111111111111111111111111111111111", "TRIANGULAR_MULTI_HOP_ARBITRAGE", "PASS", 8, 88.0, 74.0, 0.95, 20, 14, 14, 0, 1.0, 1.5, 0.3, 5.0, 0.1, 0.3, 0.0, 60.0, "", 1),
        )
        conn.execute(
            """INSERT INTO copy_trade_recommendations(
                   recommendation_id,wallet,behaviour,route_id,action,recommendation_mode,reason,
                   conservative_net_profit_base,signal_age_seconds,checks_passed,checks_failed,observed_at,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("rec1", "0x1111111111111111111111111111111111111111", "TRIANGULAR_MULTI_HOP_ARBITRAGE", "route-a", "SHADOW", "RESEARCH_ONLY", "positive conservative edge", 0.02, 2.0, 8, 0, 1, 1),
        )
        conn.commit()


def test_internal_catalogue_prioritises_learning_bot_then_sibot():
    rows = internal_source_catalogue()
    assert [row["source_id"] for row in rows] == ["INT1", "INT2"]
    assert rows[0]["source_class"] == "FIRST_PARTY_LEARNING_EVIDENCE"
    assert rows[1]["source_class"] == "FIRST_PARTY_WALLET_BEHAVIOUR"


def test_sibot_learning_is_anonymised_and_research_only(tmp_path):
    _seed(tmp_path)
    report = sibot_observed_wallet_learning(_app(tmp_path))
    assert report["source_id"] == "INT2"
    assert report["research_only"] is True
    assert report["live_execution_authorised"] is False
    assert report["wallet_identity_exposed"] is False
    chain = next(row for row in report["chains"] if row["chain_slug"] == "bsc")
    assert chain["behaviour_rankings"][0]["behaviour"] == "TRIANGULAR_MULTI_HOP_ARBITRAGE"
    assert chain["copy_wallet_candidates"][0]["status"] == "PASS"
    assert chain["recent_copy_recommendations"][0]["action"] == "SHADOW"
    encoded = json.dumps(report)
    assert "0x1111111111111111111111111111111111111111" not in encoded
    assert "wallet_" in encoded


def test_attach_internal_sources_sets_mandatory_priority(tmp_path):
    _seed(tmp_path)
    report = attach_internal_learning_sources(
        {
            "profitable_wallet_research": {"available": True},
            "cross_chain_pattern_portability": {"pattern_candidates": []},
        },
        _app(tmp_path),
    )
    assert [row["source_id"] for row in report["first_party_research_sources"]] == ["INT1", "INT2"]
    assert report["research_priority_order"][0].startswith("INT1")
    assert report["research_priority_order"][1].startswith("INT2")
    assert report["sibot_observed_wallet_learning"]["source_id"] == "INT2"
