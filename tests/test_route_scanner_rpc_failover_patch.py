from __future__ import annotations

from types import SimpleNamespace

from learnerbot import route_scanner_rpc_failover_patch as patch


A = "0x0000000000000000000000000000000000000001"
B = "0x0000000000000000000000000000000000000002"
FACTORY = "0x0000000000000000000000000000000000000003"
PAIR = "0x0000000000000000000000000000000000000004"
ZERO = "0x0000000000000000000000000000000000000000"


class RateLimitError(RuntimeError):
    def __init__(self):
        super().__init__("HTTP 429 Too Many Requests")
        self.response = SimpleNamespace(status_code=429)


class _Call:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def call(self):
        if self.error is not None:
            raise self.error
        return self.value


class _Functions:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def getPair(self, _a, _b):
        return _Call(self.value, self.error)


class _Contract:
    def __init__(self, address, value=None, error=None):
        self.address = address
        self.functions = _Functions(value, error)


class _Eth:
    def __init__(self, *, pair_value=None, pair_error=None, code=b"\x01"):
        self.pair_value = pair_value
        self.pair_error = pair_error
        self.code = code

    def contract(self, address, abi):
        return _Contract(address, self.pair_value, self.pair_error)

    def get_code(self, _address):
        return self.code


class _W3:
    def __init__(self, **kwargs):
        self.eth = _Eth(**kwargs)


class FakeScannerTrader:
    _scanner_read_only = True

    def __init__(self, first_w3, second_w3=None):
        self.w3 = first_w3
        self.second_w3 = second_w3
        self.failover_calls = 0

    def _scanner_failover(self, attempted):
        self.failover_calls += 1
        if self.second_w3 is None or self.failover_calls > 1:
            return False
        self.w3 = self.second_w3
        return True


def test_pair_lookup_rate_limit_fails_over_without_false_missing_cache(monkeypatch):
    monkeypatch.setattr(patch, "_can_failover", lambda trader: True)
    trader = FakeScannerTrader(
        _W3(pair_error=RateLimitError()),
        _W3(pair_value=PAIR),
    )
    cache = {}
    factory = SimpleNamespace(address=FACTORY)

    ok, reason = patch.path_pairs_exist_with_rpc_failover(
        trader,
        factory,
        [A, B],
        cache,
    )

    assert ok is True
    assert reason == "pairs_ok"
    assert trader.failover_calls == 1
    key = tuple(sorted((A.lower(), B.lower())))
    assert cache[key].lower() == PAIR.lower()


def test_pair_lookup_exception_is_not_cached_as_structural_absence(monkeypatch):
    monkeypatch.setattr(patch, "_can_failover", lambda trader: False)
    trader = FakeScannerTrader(_W3(pair_error=RuntimeError("provider exploded")))
    cache = {}
    factory = SimpleNamespace(address=FACTORY)

    ok, reason = patch.path_pairs_exist_with_rpc_failover(
        trader,
        factory,
        [A, B],
        cache,
    )

    assert ok is False
    assert reason.startswith("pair_lookup_provider_error:")
    key = tuple(sorted((A.lower(), B.lower())))
    assert key not in cache


def test_successful_zero_pair_is_still_cached_as_true_missing(monkeypatch):
    monkeypatch.setattr(patch, "_can_failover", lambda trader: False)
    trader = FakeScannerTrader(_W3(pair_value=ZERO))
    cache = {}
    factory = SimpleNamespace(address=FACTORY)

    ok, reason = patch.path_pairs_exist_with_rpc_failover(
        trader,
        factory,
        [A, B],
        cache,
    )

    assert ok is False
    assert reason.startswith("missing_v2_pair:")
    key = tuple(sorted((A.lower(), B.lower())))
    assert key in cache and cache[key] is None


def test_whole_scan_retries_only_transient_provider_failures(monkeypatch):
    calls = []

    def fake_scan(app, contexts):
        calls.append(1)
        if len(calls) == 1:
            raise RateLimitError()
        return "out.csv", [{"route_id": "ok"}]

    monkeypatch.setattr(patch, "_BASE_SCAN_LIVE_ROUTES", fake_scan)

    result = patch.scan_live_routes_with_rpc_failover(object(), [])

    assert result == ("out.csv", [{"route_id": "ok"}])
    assert len(calls) == 2


def test_whole_scan_does_not_retry_deterministic_failure(monkeypatch):
    calls = []

    def fake_scan(app, contexts):
        calls.append(1)
        raise RuntimeError("deterministic route failure")

    monkeypatch.setattr(patch, "_BASE_SCAN_LIVE_ROUTES", fake_scan)

    try:
        patch.scan_live_routes_with_rpc_failover(object(), [])
    except RuntimeError as exc:
        assert "deterministic route failure" in str(exc)
    else:
        raise AssertionError("deterministic failure must propagate")

    assert len(calls) == 1
