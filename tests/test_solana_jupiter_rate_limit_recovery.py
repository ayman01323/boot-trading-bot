from types import SimpleNamespace

import pytest

from learnerbot import solana_jupiter_rate_limit_patch as patch


class _Executor:
    address = "11111111111111111111111111111111"
    app = SimpleNamespace()


class _Response:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = dict(headers or {})
        self.text = str(payload)

    def json(self):
        return self._payload


def _clock(monkeypatch):
    now = [1000.0]
    sleeps = []

    def monotonic():
        return now[0]

    def sleep(seconds):
        seconds = float(seconds)
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(patch.time, "monotonic", monotonic)
    monkeypatch.setattr(patch.time, "sleep", sleep)
    patch._RATE_LIMIT_UNTIL = 0.0
    return sleeps


def _cfg(monkeypatch, retries="2", base="1", maximum="8"):
    monkeypatch.setattr(
        patch._eff,
        "_cfg",
        lambda app: {
            "live_jupiter_429_inline_retries": retries,
            "live_jupiter_429_base_delay_seconds": base,
            "live_jupiter_429_max_inline_delay_seconds": maximum,
        },
    )
    monkeypatch.setattr(patch._eff, "_headers", lambda executor: {})


def test_429_retry_after_recovers_without_changing_request(monkeypatch):
    sleeps = _clock(monkeypatch)
    _cfg(monkeypatch)
    calls = []
    responses = [
        _Response(429, {"message": "Too many requests"}, {"Retry-After": "2"}),
        _Response(200, {"outAmount": "123", "transaction": "AA=="}),
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params or {}))
        return responses.pop(0)

    monkeypatch.setattr(patch._recovery.requests, "get", fake_get)
    params = {"inputMint": "a", "outputMint": "b", "amount": "100", "slippageBps": "50"}
    result = patch.get_json_with_bounded_429_recovery(_Executor(), params, context="order")

    assert len(calls) == 2
    assert calls[0] == params
    assert calls[1] == params
    assert sleeps == [2.0]
    assert result["_jupiter_rate_limit_recovered"] is True
    assert result["_jupiter_429_retries"] == 1
    assert result["_jupiter_429_wait_seconds"] == 2.0


def test_persistent_429_uses_bounded_exponential_backoff(monkeypatch):
    sleeps = _clock(monkeypatch)
    _cfg(monkeypatch, retries="2", base="1", maximum="8")
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params or {}))
        return _Response(429, {"message": "Too many requests"})

    monkeypatch.setattr(patch._recovery.requests, "get", fake_get)
    params = {"inputMint": "a", "outputMint": "b", "amount": "100"}
    with pytest.raises(patch._exec.SolanaLiveError) as exc:
        patch.get_json_with_bounded_429_recovery(_Executor(), params, context="order")

    assert len(calls) == 3
    assert calls == [params, params, params]
    assert sleeps == [1.0, 2.0]
    assert "HTTP 429 after 2 bounded retries" in str(exc.value)


def test_long_retry_after_defers_instead_of_blocking_or_hammering(monkeypatch):
    sleeps = _clock(monkeypatch)
    _cfg(monkeypatch, retries="2", base="1", maximum="8")
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params or {}))
        return _Response(429, {"message": "Too many requests"}, {"Retry-After": "30"})

    monkeypatch.setattr(patch._recovery.requests, "get", fake_get)
    with pytest.raises(patch._exec.SolanaLiveError) as exc:
        patch.get_json_with_bounded_429_recovery(_Executor(), {"amount": "100"}, context="order")

    assert len(calls) == 1
    assert sleeps == []
    assert "Retry-After=30.00s exceeds bounded inline wait 8.00s" in str(exc.value)


def test_non_429_http_errors_are_not_retried(monkeypatch):
    sleeps = _clock(monkeypatch)
    _cfg(monkeypatch)
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params or {}))
        return _Response(400, {"error": "bad request"})

    monkeypatch.setattr(patch._recovery.requests, "get", fake_get)
    with pytest.raises(patch._exec.SolanaLiveError) as exc:
        patch.get_json_with_bounded_429_recovery(_Executor(), {"amount": "100"}, context="order")

    assert len(calls) == 1
    assert sleeps == []
    assert "HTTP 400" in str(exc.value)


def test_recovery_module_uses_rate_limit_wrapper():
    assert patch._recovery._get_json is patch.get_json_with_bounded_429_recovery
