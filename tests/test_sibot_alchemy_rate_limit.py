from types import SimpleNamespace

import pytest

from learnerbot import sibot_alchemy_rate_limit_patch as patch


SECRET_URL = "https://example.g.alchemy.com/v2/private-test-key"
WALLET = "0x0000000000000000000000000000000000000001"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_http_429_retries_with_retry_after_then_succeeds(monkeypatch):
    responses = [
        FakeResponse(429, {"error": "limited"}, {"Retry-After": "1.5"}),
        FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}),
    ]
    sleeps = []
    monkeypatch.setattr(patch.requests, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(patch.time, "sleep", lambda seconds: sleeps.append(seconds))
    result = patch._post_json(SECRET_URL, {"x": 1}, 10, "test")
    assert result["result"]["ok"] is True
    assert sleeps == [1.5]


def test_json_rpc_429_retries_then_succeeds(monkeypatch):
    responses = [
        FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "error": {"code": 429, "message": "compute units per second capacity"}}),
        FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": "ok"}),
    ]
    monkeypatch.setattr(patch.requests, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(patch.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(patch.random, "uniform", lambda a, b: 0.0)
    assert patch._post_json(SECRET_URL, {}, 10, "eth_getTransactionReceipt")["result"] == "ok"


def test_batch_item_429_retries_entire_read_only_batch(monkeypatch):
    responses = [
        FakeResponse(200, [
            {"jsonrpc": "2.0", "id": 1, "result": {"hash": "0x1"}},
            {"jsonrpc": "2.0", "id": 2, "error": {"code": 429, "message": "rate limit"}},
        ]),
        FakeResponse(200, [
            {"jsonrpc": "2.0", "id": 1, "result": {"hash": "0x1"}},
            {"jsonrpc": "2.0", "id": 2, "result": {"hash": "0x2"}},
        ]),
    ]
    monkeypatch.setattr(patch.requests, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(patch.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(patch.random, "uniform", lambda a, b: 0.0)
    result = patch._post_json(SECRET_URL, [{"id": 1}, {"id": 2}], 10, "batch")
    assert result[1]["result"]["hash"] == "0x2"


def test_retry_exhaustion_is_fail_closed_and_secret_safe(monkeypatch):
    monkeypatch.setattr(
        patch.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(429, {"error": "limited"}),
    )
    monkeypatch.setattr(patch.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(patch.random, "uniform", lambda a, b: 0.0)
    with pytest.raises(RuntimeError) as excinfo:
        patch._post_json(SECRET_URL, {}, 10, "alchemy_getAssetTransfers")
    text = str(excinfo.value)
    assert "retries exhausted" in text
    assert "HTTP 429" in text
    assert "private-test-key" not in text
    assert SECRET_URL not in text


def test_tx_context_limits_rpc_batch_size(monkeypatch):
    calls = []
    monkeypatch.setattr(patch.time, "sleep", lambda seconds: None)

    def fake_batch(url, method, params_rows, timeout=45):
        calls.append((method, len(params_rows)))
        if method == "eth_getTransactionByHash":
            return [{"from": WALLET, "to": "0x2", "value": "0x0", "gasPrice": "0x1"} for _ in params_rows]
        if method == "eth_getTransactionReceipt":
            return [{"status": "0x1", "gasUsed": "0x1", "effectiveGasPrice": "0x1"} for _ in params_rows]
        return [{"timestamp": "0x1"} for _ in params_rows]

    monkeypatch.setattr(patch._alchemy, "_batch_rpc", fake_batch)
    transfers = [
        {
            "hash": f"0x{i:064x}",
            "metadata": {"blockTimestamp": "2026-08-22T22:00:00Z"},
            "blockNum": hex(i + 1),
        }
        for i in range(23)
    ]
    normal, hashes, _ = patch._tx_context(SECRET_URL, transfers, WALLET)
    assert len(normal) == 23
    assert len(hashes) == 23
    assert max(size for _method, size in calls) <= patch._RPC_BATCH_SIZE
    assert [size for method, size in calls if method == "eth_getTransactionByHash"] == [10, 10, 3]
    assert [size for method, size in calls if method == "eth_getTransactionReceipt"] == [10, 10, 3]
