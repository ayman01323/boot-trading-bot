from __future__ import annotations

import json
from types import SimpleNamespace

from learnerbot.strategy_research_ingestion import (
    ingest_research_payload,
    load_cached_payload,
    promotion_research_gate,
    record_challenge_result,
    validate_worker_payload,
)
from learnerbot.strategy_research_worker import (
    WORKER_IDENTITY,
    build_worker_payload,
    classify_research_question,
    run_research_worker,
)


class FakeResponse:
    def __init__(self, *, payload=None, body: bytes | None = None, status=200):
        self._payload = payload
        self.content = body if body is not None else json.dumps(payload).encode("utf-8")
        self.status_code = status
        self.headers = {"Content-Length": str(len(self.content))}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._payload is None:
            return json.loads(self.content.decode("utf-8"))
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/v2/chains"):
            return FakeResponse(payload=[
                {"name": "Ethereum", "tvl": 1000.0, "tokenSymbol": "ETH", "gecko_id": "ethereum"},
                {"name": "Solana", "tvl": 500.0, "tokenSymbol": "SOL", "gecko_id": "solana"},
            ])
        if url.endswith("/overview/dexs"):
            return FakeResponse(payload={
                "total24h": 2500.0,
                "total7d": 15000.0,
                "allChains": ["Ethereum", "Solana"],
                "protocols": [
                    {"name": "DEX A", "displayName": "DEX A", "total24h": 1500.0, "total7d": 9000.0, "change_1d": 10.0, "chains": ["Ethereum"]},
                ],
            })
        if "/search/repositories" in url:
            return FakeResponse(payload={
                "total_count": 1,
                "items": [{
                    "full_name": "example/research-bot",
                    "html_url": "https://github.com/example/research-bot",
                    "description": "Public research architecture",
                    "stargazers_count": 123,
                    "forks_count": 9,
                    "archived": False,
                    "updated_at": "2026-08-23T00:00:00Z",
                    "language": "Python",
                    "license": {"spdx_id": "MIT"},
                }],
            })
        if "export.arxiv.org" in url:
            return FakeResponse(body=b'''<?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>https://arxiv.org/abs/2608.00001</id>
                <updated>2026-08-20T00:00:00Z</updated>
                <published>2026-08-20T00:00:00Z</published>
                <title>Crypto Market Microstructure</title>
                <summary>Execution evidence.</summary>
                <author><name>Researcher One</name></author>
              </entry>
            </feed>''')
        raise AssertionError(f"unexpected URL {url}")


def _app(tmp_path):
    return SimpleNamespace(data_dir=str(tmp_path))


def test_classifier_routes_current_fact_to_fresh_web():
    row = classify_research_question("What are the current Solana fees and liquidity conditions?")
    assert row["route"] == "WEB_REQUIRED"
    assert row["freshness_class"] == "FRESH_WEB"
    assert row["ttl_seconds"] == 6 * 60 * 60


def test_classifier_keeps_internal_question_off_web():
    row = classify_research_question("Review our backtest and git SHA for this strategy")
    assert row["route"] == "REPO_HISTORY_ONLY"
    assert row["freshness_class"] == "REPO_ONLY"


def test_worker_identity_is_read_only_and_returns_provenance():
    payload = run_research_worker(
        question="Check current market liquidity and protocol context",
        hypothesis_id="strat_test",
        session=FakeSession(),
        now=1_800_000_000,
    )
    assert payload["worker_identity"] == WORKER_IDENTITY
    assert payload["write_authority"] is False
    assert payload["repo_write_authority"] is False
    assert payload["config_write_authority"] is False
    assert payload["trading_authority"] is False
    assert payload["live_execution_authorised"] is False
    assert payload["wallet_signing_authority"] is False
    assert payload["external_content_instruction_authority"] is False
    assert len(payload["findings"]) == 4
    assert len(payload["source_snapshots"]) == 4
    assert all(row["url"].startswith("https://") for row in payload["findings"])
    assert all(row["retrieved_by"] == WORKER_IDENTITY for row in payload["findings"])
    assert all(row["instruction_authority"] is False for row in payload["findings"])
    assert all(len(row["supporting_excerpt"]) <= 700 for row in payload["findings"])
    validate_worker_payload(payload)


def test_repo_only_worker_does_not_fetch_web():
    session = FakeSession()
    payload = run_research_worker(
        question="Review our backtest and repository history",
        hypothesis_id="strat_internal",
        session=session,
        now=1_800_000_000,
    )
    assert payload["route"] == "REPO_HISTORY_ONLY"
    assert payload["findings"] == []
    assert session.calls == []


def test_trusted_ingestion_is_cache_first_and_expiry_aware(tmp_path):
    app = _app(tmp_path)
    payload = run_research_worker(
        question="Check current market liquidity",
        hypothesis_id="strat_cache",
        session=FakeSession(),
        now=1_800_000_000,
    )
    result = ingest_research_payload(app, payload)
    assert result["stored"] is True
    assert load_cached_payload(
        app,
        hypothesis_id="strat_cache",
        question="Check current market liquidity",
        now=1_800_000_100,
    ) is not None
    assert load_cached_payload(
        app,
        hypothesis_id="strat_cache",
        question="Check current market liquidity",
        now=1_800_000_000 + 6 * 60 * 60 + 1,
    ) is None


def test_capital_gate_fails_closed_for_missing_or_expired_research():
    missing = promotion_research_gate(None, target_stage="CANARY", now=1_800_000_000)
    assert missing["eligible"] is False
    assert "no validated research payload" in missing["reasons"]

    pack = {
        "evidence_sha256": "a" * 64,
        "sources": [{
            "source_id": "OFF1",
            "source_class": "OFFICIAL_API_WEBSOCKET",
            "canonical_url": "https://api.llama.fi/v2/chains",
            "retrieved_utc": "2026-08-23T00:00:00+00:00",
            "data_sha256": "b" * 64,
            "data": {"value": 1},
            "notes": "Official current protocol evidence.",
        }],
        "errors": [],
    }
    payload = build_worker_payload(
        question="current protocol fees",
        hypothesis_id="strat_gate",
        external_pack=pack,
        now=1_800_000_000,
    )
    canary = promotion_research_gate(payload, target_stage="CANARY", now=1_800_000_100)
    assert canary["eligible"] is True
    expired = promotion_research_gate(payload, target_stage="CANARY", now=1_800_000_000 + 6 * 60 * 60 + 1)
    assert expired["eligible"] is False
    assert "one or more findings expired" in expired["reasons"]


def test_live_requires_independent_challenge_and_dispute_fails_closed(tmp_path):
    app = _app(tmp_path)
    pack = {
        "evidence_sha256": "a" * 64,
        "sources": [{
            "source_id": "OFF1",
            "source_class": "OFFICIAL_API_WEBSOCKET",
            "canonical_url": "https://api.llama.fi/v2/chains",
            "retrieved_utc": "2026-08-23T00:00:00+00:00",
            "data_sha256": "b" * 64,
            "data": {"value": 1},
            "notes": "Official current protocol evidence.",
        }],
        "errors": [],
    }
    payload = build_worker_payload(
        question="current protocol fees",
        hypothesis_id="strat_live",
        external_pack=pack,
        now=1_800_000_000,
    )
    ingest_research_payload(app, payload)
    assert promotion_research_gate(payload, target_stage="LIVE", now=1_800_000_100)["eligible"] is False

    record_challenge_result(
        app,
        hypothesis_id="strat_live",
        question="current protocol fees",
        status="PASS",
        challenger="GPT_CHALLENGER",
        now=1_800_000_050,
    )
    challenged = load_cached_payload(
        app,
        hypothesis_id="strat_live",
        question="current protocol fees",
        now=1_800_000_100,
    )
    assert challenged is not None
    assert promotion_research_gate(challenged, target_stage="LIVE", now=1_800_000_100)["eligible"] is True

    record_challenge_result(
        app,
        hypothesis_id="strat_live",
        question="current protocol fees",
        status="DISPUTED",
        challenger="GPT_CHALLENGER",
        now=1_800_000_060,
    )
    disputed = load_cached_payload(
        app,
        hypothesis_id="strat_live",
        question="current protocol fees",
        now=1_800_000_100,
    )
    assert disputed is not None
    gate = promotion_research_gate(disputed, target_stage="LIVE", now=1_800_000_100)
    assert gate["eligible"] is False
    assert "one or more findings disputed" in gate["reasons"]


def test_source_extension_preserves_legacy_external_rows_and_new_worker_contract(monkeypatch, tmp_path):
    from learnerbot import strategy_source_extension as extension

    app = _app(tmp_path)
    pack = {
        "evidence_sha256": "c" * 64,
        "sources": [{
            "source_id": "EXT1",
            "source_class": "PRIMARY_RAW_DATA",
            "canonical_url": "https://api.llama.fi/v2/chains",
            "retrieved_utc": "2026-08-23T00:00:00+00:00",
            "data_sha256": "d" * 64,
            "data": {"focus_chains": [{"name": "Solana"}]},
            "notes": "Primary raw market context.",
            "untrusted_external_content": True,
            "instruction_authority": False,
            "research_only": True,
        }],
        "errors": [],
    }
    worker = build_worker_payload(
        question=extension._RESEARCH_QUESTION,
        hypothesis_id=extension._RESEARCH_HYPOTHESIS_ID,
        external_pack=pack,
        now=1_800_000_000,
    )
    monkeypatch.setattr(extension, "_PREV_BUILD", lambda _app: {"baseline": True})
    monkeypatch.setattr(extension, "attach_internal_learning_sources", lambda report, _app: report)
    monkeypatch.setattr(extension, "source_catalogue", lambda: {"sources": [], "source_count": 0})
    monkeypatch.setattr(extension, "internal_source_catalogue", lambda: [])
    monkeypatch.setattr(extension, "_worker_research", lambda _app: (worker, False, {"stored": True}))

    report = extension._build_with_source_governance(app)
    legacy = report["external_source_research"]
    assert legacy["source_ids"] == ["EXT1"]
    assert legacy["sources"][0]["data"] == {"focus_chains": [{"name": "Solana"}]}
    assert report["online_research_worker"]["findings"][0]["url"] == "https://api.llama.fi/v2/chains"
    assert report["research_promotion_gates"]["CANARY"]["eligible"] is True
    assert report["research_promotion_gates"]["LIVE"]["eligible"] is False
