from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from learnerbot import strategy_source_extension as source_extension
from learnerbot.strategy_external_research import (
    _safe_url,
    collect_external_strategy_research,
    fetch_github_research,
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
    def __init__(self, *, arxiv_status=200):
        self.calls = []
        self.arxiv_status = arxiv_status

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/v2/chains"):
            return FakeResponse(payload=[
                {"name": "Ethereum", "tvl": 1000.0, "tokenSymbol": "ETH", "gecko_id": "ethereum"},
                {"name": "Solana", "tvl": 500.0, "tokenSymbol": "SOL", "gecko_id": "solana"},
                {"name": "Base", "tvl": 200.0, "tokenSymbol": None, "gecko_id": None},
            ])
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
                    "updated_at": "2026-08-19T00:00:00Z",
                    "language": "Python",
                    "license": {"spdx_id": "MIT"},
                }],
            })
        if "export.arxiv.org" in url:
            body = b'''<?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>https://arxiv.org/abs/2608.00001</id>
                <updated>2026-08-19T00:00:00Z</updated>
                <published>2026-08-19T00:00:00Z</published>
                <title>Crypto Market Microstructure</title>
                <summary>Evidence about market microstructure and execution.</summary>
                <author><name>Researcher One</name></author>
              </entry>
            </feed>'''
            return FakeResponse(body=body, status=self.arxiv_status)
        raise AssertionError(f"unexpected URL {url}")


def test_safe_url_is_strict_allowlist():
    assert _safe_url("https://api.llama.fi/v2/chains").startswith("https://api.llama.fi/")
    with pytest.raises(ValueError):
        _safe_url("http://api.llama.fi/v2/chains")
    with pytest.raises(ValueError):
        _safe_url("https://127.0.0.1/private")
    with pytest.raises(ValueError):
        _safe_url("https://example.com/")


def test_external_pack_is_bounded_hashed_and_research_only():
    session = FakeSession()
    pack = collect_external_strategy_research(github_token="TOP-SECRET", session=session)
    assert pack["source_ids"] == ["EXT1", "EXT2", "EXT3"]
    assert len(pack["evidence_sha256"]) == 64
    assert pack["research_only"] is True
    assert pack["live_execution_authorised"] is False
    assert pack["external_content_instruction_authority"] is False
    serialised = json.dumps(pack)
    assert "TOP-SECRET" not in serialised
    assert "research-bot" in serialised
    assert "Crypto Market Microstructure" in serialised


def test_github_token_is_used_only_as_request_header():
    session = FakeSession()
    result = fetch_github_research(session, token="TOP-SECRET")
    github_calls = [call for call in session.calls if "/search/repositories" in call[0]]
    assert len(github_calls) == 1
    headers = github_calls[0][1]["headers"]
    assert headers["Authorization"] == "Bearer TOP-SECRET"
    assert "TOP-SECRET" not in json.dumps(result)


def test_partial_source_failure_is_recorded_not_fatal():
    session = FakeSession(arxiv_status=503)
    pack = collect_external_strategy_research(session=session)
    assert [row["source_id"] for row in pack["sources"]] == ["EXT1", "EXT2"]
    assert pack["errors"][0]["source_id"] == "EXT3"
    assert pack["live_execution_authorised"] is False


def test_defillama_payload_is_compacted_not_dumped_wholesale():
    session = FakeSession()
    pack = collect_external_strategy_research(session=session)
    defi = next(row for row in pack["sources"] if row["source_id"] == "EXT1")
    names = {row["name"] for row in defi["data"]["focus_chains"]}
    assert {"Ethereum", "Solana", "Base"} <= names
    assert defi["data"]["chain_count"] == 3


def test_strategy_report_contains_fresh_external_pack_before_agent_review(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    csv_dir = tmp_path / "csv"
    data_dir.mkdir()
    csv_dir.mkdir()
    app = SimpleNamespace(data_dir=data_dir, csv_dir=csv_dir)
    stub = {
        "schema_version": 1,
        "sources": [{"source_id": "EXT1", "name": "stub"}],
        "errors": [],
        "research_only": True,
        "live_execution_authorised": False,
        "external_content_instruction_authority": False,
        "evidence_sha256": "a" * 64,
    }
    monkeypatch.setattr(source_extension, "collect_external_strategy_research", lambda: stub)
    report = source_extension._build_with_source_governance(app)
    assert report["external_source_research"] == stub
    assert report["fresh_external_source_count"] == 1
    assert "BEFORE" in report["ai_source_discovery_instruction"]
    assert "EXT" in report["ai_source_discovery_instruction"]
