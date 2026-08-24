from decimal import Decimal
from types import SimpleNamespace

import pytest

from learnerbot.live_executor import LiveTrader, LiveTradingError
from learnerbot.auto_trader import _append_simulation, _rows


class _Fn:
    def __init__(self):
        self.args = None
    def build_transaction(self, tx):
        out = dict(tx)
        out.update({"to": "0x00000000000000000000000000000000000000aa", "data": "0x1234", "value": 0})
        return out


class _Functions:
    def __init__(self, fn):
        self.fn = fn
    def swapExactTokensForTokensSupportingFeeOnTransferTokens(self, *args):
        self.fn.args = args
        return self.fn


class _Eth:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
    def call(self, tx):
        self.calls.append(dict(tx))
        if self.fail:
            raise RuntimeError("simulated revert")
        return b""


def _trader(fail_call=False):
    t = LiveTrader.__new__(LiveTrader)
    p = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
        "0x0000000000000000000000000000000000000001",
    ]
    # LiveTrader.__init__ always sets the wrapped-native address. This synthetic
    # fixture bypasses __init__, so preserve that production invariant explicitly.
    t.wrapped = p[0]
    sim = {
        "path": p,
        "amount_in_raw": 100,
        "amount_out_raw": 140,
        "amount_in": Decimal("0.0000000000000001"),
        "amount_out": Decimal("0.00000000000000014"),
        "gross_profit": Decimal("0.00000000000000004"),
        "prepared": True,
        "simulation_ok": True,
        "gas": 100000,
        "gas_cost_base": Decimal("0.00000000000000001"),
        "min_out_raw": 120,
        "reason": "PASS",
    }
    t.simulate_cycle = lambda *a, **k: dict(sim)
    fn = _Fn()
    t.router = SimpleNamespace(functions=_Functions(fn))
    t.address = "0x0000000000000000000000000000000000000003"
    t.w3 = SimpleNamespace(eth=_Eth(fail_call))
    t._base_tx = lambda: {
        "from": t.address,
        "nonce": 9,
        "chainId": 56,
        "maxFeePerGas": 10,
        "maxPriorityFeePerGas": 1,
    }
    t._deadline = lambda: 9999999999
    t._require_enabled = lambda side: None
    t._confirm = lambda word: None
    t.chain = SimpleNamespace(explorer_url="https://example.invalid", chain_id=56, slug="bsc")
    t._audit_rows = []
    t._audit = lambda *args, **kwargs: t._audit_rows.append(args)
    t._sent = []
    t._sign_send = lambda tx: (t._sent.append(dict(tx)) or "0xabc")
    return t, sim


def test_mandatory_eth_call_preflight_passes_before_send():
    t, _ = _trader(False)
    out = t.execute_cycle([], "0.1", "0.01", "CONFIRM")
    assert out["preflight_call_ok"] is True
    assert len(t.w3.eth.calls) == 1
    assert len(t._sent) == 1
    # Signing-only metadata is not sent to eth_call.
    assert "nonce" not in t.w3.eth.calls[0]
    assert "chainId" not in t.w3.eth.calls[0]
    assert any(r[0] == "AUTO_PREFLIGHT" and r[-1] == "PASS" for r in t._audit_rows)


def test_failed_eth_call_never_signs_or_broadcasts():
    t, _ = _trader(True)
    with pytest.raises(LiveTradingError, match="Mandatory pre-broadcast eth_call simulation failed"):
        t.execute_cycle([], "0.1", "0.01", "CONFIRM")
    assert len(t.w3.eth.calls) == 1
    assert t._sent == []
    assert any(r[0] == "AUTO_PREFLIGHT" and r[-1] == "REJECTED" for r in t._audit_rows)


def test_public_preflight_never_broadcasts():
    t, _ = _trader(False)
    out = t.preflight_cycle([], "0.1", "0.01")
    assert out["simulation_ok"] is True
    assert out["preflight_call_ok"] is True
    assert len(t.w3.eth.calls) == 1
    assert t._sent == []


def test_simulation_audit_is_written(tmp_path):
    _append_simulation(tmp_path, {
        "timestamp_epoch": 1,
        "telegram_id": "123",
        "wallet_id": "main",
        "chain_id": 56,
        "chain_slug": "bsc",
        "route_id": "route",
        "route_path": "a>b>a",
        "input_base": "0.00005",
        "min_net_profit_base": "0.000001",
        "gross_profit_base": "0.000002",
        "gas_cost_base": "0.0000005",
        "simulation_ok": "true",
        "reason": "PASS",
    })
    rows = _rows(tmp_path / "auto" / "auto_trade_simulations.csv")
    assert len(rows) == 1
    assert rows[0]["simulation_ok"] == "true"
    assert rows[0]["reason"] == "PASS"
