from types import SimpleNamespace

import pytest

from learnerbot import full_power_scanner as fp
from learnerbot import full_power_scanner_rpc_failover_patch as patch
from learnerbot import live_executor as live


def _addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def test_patch_is_scanner_local_and_does_not_replace_live_executor():
    assert fp.LiveTrader is patch.ScannerFailoverLiveTrader
    assert live.LiveTrader is patch._BASE_TRADER
    assert fp._v3_quote is patch.v3_quote_with_rpc_failover


def test_retry_classifier_is_narrow():
    assert patch._rpc_error_kind(RuntimeError("HTTP 429 too many requests")) == "provider_rate_limit"
    assert patch._rpc_error_kind(RuntimeError("gateway timeout 504")) == "provider_transport"
    assert patch._rpc_error_kind(RuntimeError("execution reverted: INSUFFICIENT_LIQUIDITY")) == ""
    assert patch._rpc_error_kind(RuntimeError("contract has no matching function")) == ""


def test_endpoint_rotation_is_bounded_to_three():
    patch._RPC_CURSOR.clear()
    assert patch._rotated_indices(8453, 5) == [0, 1, 2]
    assert patch._rotated_indices(8453, 5) == [1, 2, 3]
    assert len(patch._rotated_indices(8453, 20)) == 3


def test_read_only_constructor_never_loads_signer(monkeypatch, tmp_path):
    patch._RPC_CURSOR.clear()
    chain = SimpleNamespace(
        slug="base",
        name="Base",
        enabled=True,
        chain_id=8453,
        rpc_urls=["https://rpc-one.invalid", "https://rpc-two.invalid"],
        wrapped_base_address=_addr(1),
    )
    monkeypatch.setattr(patch._live, "load_chains", lambda app, enabled_only=False: [chain])

    def fake_bind(self, index):
        self._scanner_rpc_index = int(index)
        self.w3 = object()
        self.router = object()

    monkeypatch.setattr(patch.ScannerFailoverLiveTrader, "_bind_scanner_rpc", fake_bind)
    app = SimpleNamespace(csv_dir=tmp_path)
    trader = patch.ScannerFailoverLiveTrader(app, "base", require_wallet=False)
    assert trader.account is None
    assert trader.address is None
    assert trader._scanner_read_only is True
    assert trader._scanner_rpc_index in {0, 1}


def test_v2_quote_retries_transient_provider_only(monkeypatch):
    trader = object.__new__(patch.ScannerFailoverLiveTrader)
    trader._scanner_read_only = True
    trader._scanner_rpc_urls = ["https://one.invalid", "https://two.invalid"]
    trader._scanner_rpc_index = 0
    calls = {"quote": 0, "failover": 0}

    def fake_quote(self, path, amount):
        calls["quote"] += 1
        if calls["quote"] == 1:
            raise RuntimeError("HTTP 429 too many requests")
        return {"gross_profit": 1, "amount_out": 2}

    def fake_failover(attempted):
        calls["failover"] += 1
        attempted.add(1)
        trader._scanner_rpc_index = 1
        return True

    monkeypatch.setattr(patch._BASE_TRADER, "cycle_quote", fake_quote)
    trader._scanner_failover = fake_failover
    result = trader.cycle_quote([_addr(1), _addr(2), _addr(1)], "0.001")
    assert result["gross_profit"] == 1
    assert calls == {"quote": 2, "failover": 1}


def test_v2_quote_does_not_retry_deterministic_revert(monkeypatch):
    trader = object.__new__(patch.ScannerFailoverLiveTrader)
    trader._scanner_read_only = True
    trader._scanner_rpc_urls = ["https://one.invalid", "https://two.invalid"]
    trader._scanner_rpc_index = 0
    called = {"failover": 0}

    def fake_quote(self, path, amount):
        raise RuntimeError("execution reverted: no route")

    def fake_failover(attempted):
        called["failover"] += 1
        return True

    monkeypatch.setattr(patch._BASE_TRADER, "cycle_quote", fake_quote)
    trader._scanner_failover = fake_failover
    with pytest.raises(RuntimeError, match="execution reverted"):
        trader.cycle_quote([_addr(1), _addr(2), _addr(1)], "0.001")
    assert called["failover"] == 0


def test_exhausted_v2_failover_never_exposes_rpc_url(monkeypatch):
    trader = object.__new__(patch.ScannerFailoverLiveTrader)
    trader._scanner_read_only = True
    trader._scanner_rpc_urls = ["https://secret-provider.invalid/API_KEY_SHOULD_NOT_LEAK"]
    trader._scanner_rpc_index = 0

    def fake_quote(self, path, amount):
        raise RuntimeError("HTTP 429 https://secret-provider.invalid/API_KEY_SHOULD_NOT_LEAK")

    monkeypatch.setattr(patch._BASE_TRADER, "cycle_quote", fake_quote)
    with pytest.raises(live.LiveTradingError) as caught:
        trader.cycle_quote([_addr(1), _addr(2), _addr(1)], "0.001")
    text = str(caught.value)
    assert "API_KEY_SHOULD_NOT_LEAK" not in text
    assert "http" not in text.lower()
    assert "provider_rate_limit" in text


def test_v3_quote_retries_transient_provider_only(monkeypatch):
    trader = object.__new__(patch.ScannerFailoverLiveTrader)
    trader._scanner_read_only = True
    trader._scanner_rpc_urls = ["https://one.invalid", "https://two.invalid"]
    trader._scanner_rpc_index = 0
    calls = {"quote": 0, "failover": 0}

    def fake_v3(t, quoter, path, fees, amount):
        calls["quote"] += 1
        if calls["quote"] == 1:
            raise RuntimeError("service unavailable 503")
        return {"amount_out": 2, "gross_profit": 1}

    def fake_failover(attempted):
        calls["failover"] += 1
        attempted.add(1)
        trader._scanner_rpc_index = 1
        return True

    monkeypatch.setattr(patch, "_ORIGINAL_V3_QUOTE", fake_v3)
    trader._scanner_failover = fake_failover
    result = patch.v3_quote_with_rpc_failover(trader, _addr(9), [_addr(1), _addr(2)], [500], 1)
    assert result["gross_profit"] == 1
    assert calls == {"quote": 2, "failover": 1}
