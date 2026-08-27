from __future__ import annotations

from types import SimpleNamespace

import pytest

from learnerbot import solana_sibot as sol
from learnerbot import solana_rpc_failover_patch as failover


class FakeResponse:
    def __init__(self, status_code: int, body=None, headers=None):
        self.status_code = int(status_code)
        self._body = body if body is not None else {}
        self.headers = headers or {}

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
        "SOLANA_RPC_429_COOLDOWN_SECONDS",
        "SOLANA_RPC_429_MAX_COOLDOWN_SECONDS",
        "SOLANA_RPC_TRANSIENT_COOLDOWN_SECONDS",
        "SOLANA_RPC_MAX_INFLIGHT_PER_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _reset_endpoint_health():
    failover._reset_endpoint_health_for_tests()
    yield
    failover._reset_endpoint_health_for_tests()


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


def test_429_endpoint_is_skipped_during_cooldown(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setenv("SOLANA_RPC_429_COOLDOWN_SECONDS", "30")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(429, {"error": "rate limited"})
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": 123})

    monkeypatch.setattr(failover.requests, "post", post)
    assert failover.rpc_failover(_app(tmp_path), "getSlot", []) == 123
    assert failover.rpc_failover(_app(tmp_path), "getSlot", []) == 123
    assert calls == [first, second, second]


def test_all_cooling_endpoints_fail_without_more_http_requests(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    monkeypatch.setenv("SOLANA_RPC_URL", first)
    monkeypatch.setenv("SOLANA_RPC_429_COOLDOWN_SECONDS", "30")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return FakeResponse(429, {"error": "rate limited"})

    monkeypatch.setattr(failover.requests, "post", post)
    with pytest.raises(failover.SolanaRpcEndpointError) as caught:
        failover.rpc_failover(_app(tmp_path), "getSlot", [])
    assert caught.value.status_code == 429
    first_call_count = len(calls)
    assert first_call_count == 2

    with pytest.raises(failover.SolanaRpcEndpointError) as caught_again:
        failover.rpc_failover(_app(tmp_path), "getSlot", [])
    assert "cooling" in str(caught_again.value).lower()
    assert len(calls) == first_call_count


def test_retry_after_header_extends_429_cooldown(monkeypatch, tmp_path):
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    monkeypatch.setenv("SOLANA_RPC_URL", first)
    monkeypatch.setenv("SOLANA_RPC_429_COOLDOWN_SECONDS", "5")
    monkeypatch.setenv("SOLANA_RPC_429_MAX_COOLDOWN_SECONDS", "120")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})

    fake_now = [1000.0]
    monkeypatch.setattr(failover.time, "monotonic", lambda: fake_now[0])

    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(429, {"error": "rate limited"}, {"Retry-After": "45"})
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": 7})

    monkeypatch.setattr(failover.requests, "post", post)
    assert failover.rpc_failover(_app(tmp_path), "getSlot", []) == 7

    state = failover._ENDPOINT_STATE[first]
    assert float(state["cooldown_until"]) == pytest.approx(1045.0)


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


# ---------------------------------------------------------------------------
# New tests: 401/403 auth-failure endpoint-local failover (issue #671 part A)
# ---------------------------------------------------------------------------

def test_primary_401_fails_over_to_secondary(monkeypatch, tmp_path):
    """primary 401 + healthy secondary => request succeeds via secondary."""
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(401, b"Unauthorized")
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": {"value": 42}})

    monkeypatch.setattr(failover.requests, "post", post)
    result = failover.rpc_failover(_app(tmp_path), "getBalance", ["wallet"])
    assert result == {"value": 42}
    assert calls == [first, second]


def test_primary_403_fails_over_to_secondary(monkeypatch, tmp_path):
    """primary 403 + healthy secondary => request succeeds via secondary."""
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(403, b"Forbidden")
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": 99})

    monkeypatch.setattr(failover.requests, "post", post)
    result = failover.rpc_failover(_app(tmp_path), "getSlot", [])
    assert result == 99
    assert calls == [first, second]


def test_all_endpoints_401_fail_closed_with_sanitised_error(monkeypatch, tmp_path):
    """all configured endpoints 401/403 => sanitised fail-closed result."""
    _clear_rpc_env(monkeypatch)
    secret = "api-key-must-not-appear"
    first = f"https://rpc-one.example/?key={secret}"
    second = f"https://rpc-two.example/?key={secret}"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})

    def post(url, **kwargs):
        return FakeResponse(401, b"Unauthorized")

    monkeypatch.setattr(failover.requests, "post", post)
    with pytest.raises(failover.SolanaRpcEndpointError) as caught:
        failover.rpc_failover(_app(tmp_path), "getBalance", ["wallet"])
    err = caught.value
    assert err.auth_failure is True
    assert err.transient is False
    text = str(err)
    assert secret not in text
    assert first not in text
    assert second not in text
    assert "auth" in text.lower() or "401" in text


def test_401_endpoint_is_quarantined_after_auth_failure(monkeypatch, tmp_path):
    """After a 401, the endpoint is placed in cooldown so subsequent cycles skip it."""
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})

    fake_now = [0.0]
    monkeypatch.setattr(failover.time, "monotonic", lambda: fake_now[0])
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(401, b"Unauthorized")
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": "ok"})

    monkeypatch.setattr(failover.requests, "post", post)
    assert failover.rpc_failover(_app(tmp_path), "getSlot", []) == "ok"
    assert calls == [first, second]

    # Next call: first endpoint still in auth cooldown; only second is tried
    result2 = failover.rpc_failover(_app(tmp_path), "getSlot", [])
    assert result2 == "ok"
    assert calls == [first, second, second]


def test_401_not_retried_same_request_cycle(monkeypatch, tmp_path):
    """A 401 endpoint must not be retried in the same request cycle."""
    _clear_rpc_env(monkeypatch)
    endpoint = "https://rpc-only.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", endpoint)
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return FakeResponse(401, b"Unauthorized")

    monkeypatch.setattr(failover.requests, "post", post)
    with pytest.raises(failover.SolanaRpcEndpointError) as caught:
        failover.rpc_failover(_app(tmp_path), "getBalance", ["addr"])
    # Each URL must appear at most once — the 401 endpoint is not retried
    assert calls.count(endpoint) == 1
    assert caught.value.auth_failure is True


def test_429_behaviour_unchanged_by_auth_failure_patch(monkeypatch, tmp_path):
    """Ensure 429 cooldown/backoff behaviour is unaffected by the auth-failure changes."""
    _clear_rpc_env(monkeypatch)
    first = "https://rpc-one.example"
    second = "https://rpc-two.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", f"{first},{second}")
    monkeypatch.setenv("SOLANA_RPC_429_COOLDOWN_SECONDS", "30")
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == first:
            return FakeResponse(429, {"error": "rate limited"})
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": "ok"})

    monkeypatch.setattr(failover.requests, "post", post)
    assert failover.rpc_failover(_app(tmp_path), "getSlot", []) == "ok"
    assert failover.rpc_failover(_app(tmp_path), "getSlot", []) == "ok"
    # first called once (429), second called twice (both requests)
    assert calls == [first, second, second]


def test_auth_failure_secret_not_in_error_text(monkeypatch, tmp_path):
    """No secret leakage in exception/log strings from 401/403."""
    _clear_rpc_env(monkeypatch)
    secret = "super-secret-rpc-key"
    endpoint = f"https://rpc.example/?api-key={secret}"
    monkeypatch.setenv("SOLANA_RPC_URLS", endpoint)
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    monkeypatch.setattr(
        failover.requests,
        "post",
        lambda url, **kwargs: FakeResponse(403, b"Forbidden"),
    )

    with pytest.raises(failover.SolanaRpcEndpointError) as caught:
        failover.rpc_failover(_app(tmp_path), "getBalance", ["wallet"])
    text = str(caught.value)
    assert secret not in text
    assert endpoint not in text


def test_endpoint_ordering_and_public_fallback_correct(monkeypatch, tmp_path):
    """Configured endpoints are tried before the public fallback."""
    _clear_rpc_env(monkeypatch)
    configured = "https://rpc-configured.example"
    monkeypatch.setenv("SOLANA_RPC_URLS", configured)
    monkeypatch.setattr(sol, "settings", lambda app: {"rpc_url": sol.DEFAULT_RPC})
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == configured:
            return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": "configured-ok"})
        return FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": "public-ok"})

    monkeypatch.setattr(failover.requests, "post", post)
    result = failover.rpc_failover(_app(tmp_path), "getSlot", [])
    assert result == "configured-ok"
    # Only the configured endpoint was called; public not needed
    assert calls == [configured]
    assert sol.DEFAULT_RPC not in calls
