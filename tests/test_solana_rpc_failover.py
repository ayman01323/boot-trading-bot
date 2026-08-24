from __future__ import annotations

from types import SimpleNamespace

import pytest

from learnerbot import solana_sibot as sol
from learnerbot import solana_rpc_failover_patch as failover


class FakeResponse:
    def __init__(self, status_code: int, body=None):
        self.status_code = int(status_code)
        self._body = body if body is not None else {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def _clear_rpc_env(monkeypatch):
    for key in (
        "SOLANA_RPC_URLS",
        "SOLANA_RPC_FALLBACK_URLS",
        "SOLANA_RPC_URL",
        "HELIUS_RPC_URL",
        "HELIUS_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_helius_is_preferred_to_public_rpc_when_key_exists(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv("HELIUS_API_KEY", "secret-test-key")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    urls = failover._candidate_urls(_app(tmp_path))
    assert urls[0].startswith("https://mainnet.helius-rpc.com/")
    assert "secret-test-key" in urls[0]
    assert urls[-1] == sol.DEFAULT_RPC


def test_transient_429_fails_over_to_next_endpoint(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(429, {"error": "rate limited"})
        if url == second:
            return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": {"value": 7}})
        raise AssertionError(f"unexpected endpoint {url}")

    monkeypatch.setattr(failover.requests, "post", post)
    result = failover.rpc_failover(_app(tmp_path), "getBalance", ["wallet"])
    assert result == {"value": 7}
    assert calls == [first, second]


def test_non_transient_http_400_does_not_fail_over(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return FakeResponse(400, {"error": "invalid request"})

    monkeypatch.setattr(failover.requests, "post", post)
    with pytest.raises(failover.SolanaRpcEndpointError) as caught:
        failover.rpc_failover(_app(tmp_path), "badMethod", [])
    assert calls == [first]
    assert caught.value.transient is False
    assert caught.value.status_code == 400


def test_error_text_never_contains_endpoint_or_api_key(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    secret = "super-secret-api-key"
    endpoint = f"https://rpc.example/?api-key={secret}"
    monkeypatch.setenv("SOLANA_RPC_URLS", endpoint)
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    monkeypatch.setattr(
        failover.requests,
        "post",
        lambda url, **kwargs: FakeResponse(429, {"error": "rate limited"}),
    )

    with pytest.raises(failover.SolanaRpcEndpointError) as caught:
        failover.rpc_failover(_app(tmp_path), "getSignaturesForAddress", ["wallet"])
    text = str(caught.value)
    assert secret not in text
    assert endpoint not in text
    assert "HTTP 429" in text


def test_transient_json_rpc_error_fails_over(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first};{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "error": {"message": "Node is behind"}})
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": ["ok"]})

    monkeypatch.setattr(failover.requests, "post", post)
    assert failover.rpc_failover(_app(tmp_path), "getSlot", []) == ["ok"]
    assert calls == [first, second]


def test_node_unhealthy_json_rpc_error_fails_over(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "error": {"message": "Node is unhealthy"}})
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": 123})

    monkeypatch.setattr(failover.requests, "post", post)
    assert failover.rpc_failover(_app(tmp_path), "getSlot", []) == 123
    assert calls == [first, second]


def test_malformed_json_rpc_envelope_fails_over_instead_of_returning_none(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(200, {"jsonrpc": "2.0", "id": 1})
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": ["fallback-ok"]})

    monkeypatch.setattr(failover.requests, "post", post)
    assert failover.rpc_failover(_app(tmp_path), "getSignaturesForAddress", ["wallet"]) == ["fallback-ok"]
    assert calls == [first, second]


def test_non_object_json_rpc_envelope_fails_over(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(200, ["not", "a", "json-rpc", "object"])
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": "ok"})

    monkeypatch.setattr(failover.requests, "post", post)
    assert failover.rpc_failover(_app(tmp_path), "getHealth", []) == "ok"
    assert calls == [first, second]
