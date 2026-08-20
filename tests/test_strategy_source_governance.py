from __future__ import annotations

from learnerbot.strategy_source_catalog import CURATED_STRATEGY_SOURCES, SOURCE_DISCOVERY_POLICY, source_catalogue
from learnerbot.strategy_source_contract import enforce_source_policy, validate_agent_report


def _source_row(**overrides):
    row = {
        "id": "official-test-source",
        "name": "Official Test Source",
        "source_class": "PRIMARY_RAW_DATA",
        "official_url": "https://example.org/data",
        "publisher": "Example Publisher",
        "chain_scope": ["EVM", "SOLANA"],
        "access_model": "public API",
        "intended_use": "read-only market research",
        "trust_basis": "canonical publisher documentation",
        "risks": ["rate limits"],
        "confidence": 0.92,
        "research_only": True,
        "automatic_execution_allowed": False,
    }
    row.update(overrides)
    return row


def _agent(provider="gpt"):
    return {
        "schema_version": 1,
        "provider": provider,
        "cycle_id": "abc123def456-20260820",
        "source_commit": "abc123",
        "scope": "STRATEGY_SOURCE_RESEARCH",
        "status": "CHANGES_PROPOSED",
        "summary": "test",
        "source_recommendations": [_source_row()],
        "rejected_sources": [],
        "research_gaps": [],
        "research_only": True,
        "no_live_changes": True,
    }


def _master(**overrides):
    row = _source_row(
        disposition="ACCEPT",
        reason="two independent agents verified canonical provenance",
        supporting_agents=["gpt", "gemini"],
    )
    row.update(overrides)
    return {
        "schema_version": 1,
        "scope": "STRATEGY_SOURCE_MASTER",
        "cycle_id": "abc123def456-20260820",
        "source_commit": "abc123",
        "summary": "test",
        "source_decisions": [row],
        "research_only": True,
        "no_live_changes": True,
    }


def test_curated_catalogue_contains_requested_primary_framework_and_infra_sources():
    names = {row["tool"] for row in CURATED_STRATEGY_SOURCES}
    required = {
        "Binance Public Data", "Tardis.dev", "Cryptofeed", "CCXT", "CCXT Pro / WebSocket API",
        "Freqtrade", "Jesse", "Backtrader", "VectorBT", "Hummingbot", "Web3.py", "ethers.js",
        "Foundry", "The Graph", "Flashbots", "QuantConnect LEAN", "SSRN", "arXiv",
    }
    assert required <= names
    assert source_catalogue()["live_execution_authorised"] is False
    assert SOURCE_DISCOVERY_POLICY["consensus"]["minimum_independent_agents"] == 2
    assert "influencer_or_social_media_trade_calls" in SOURCE_DISCOVERY_POLICY["avoid"]


def test_agent_source_report_requires_canonical_research_only_shape():
    validate_agent_report(_agent(), provider="gpt", cycle_id="abc123def456-20260820", source_commit="abc123")


def test_source_policy_accepts_two_agent_high_confidence_research_source():
    gated = enforce_source_policy(_master())
    assert gated["approved_source_count"] == 1
    assert gated["source_decisions"][0]["policy_approved"] is True
    assert gated["policy"]["automatic_execution"] is False


def test_source_policy_rejects_single_agent_low_confidence_or_execution_permission():
    one = enforce_source_policy(_master(supporting_agents=["gpt"]))
    assert one["approved_source_count"] == 0
    low = enforce_source_policy(_master(confidence=0.5))
    assert low["approved_source_count"] == 0
    live = enforce_source_policy(_master(automatic_execution_allowed=True))
    assert live["approved_source_count"] == 0
