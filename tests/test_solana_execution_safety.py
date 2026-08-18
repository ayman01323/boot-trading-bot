from decimal import Decimal
from types import SimpleNamespace

import pytest

from learnerbot import solana_live_executor as live_exec
from learnerbot import solana_live_patch as live
from learnerbot import solana_sibot as sol
from learnerbot.user_registry import set_user_setting, user_bool


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _executor(monkeypatch, payload, balances=(10_000_000, 9_000_000)):
    ex = live_exec.SolanaLiveExecutor.__new__(live_exec.SolanaLiveExecutor)
    ex.app = object()
    ex.telegram_id = "123"
    ex.address = "wallet"
    ex.keypair = b"key"
    seq = iter(balances)
    ex.native_balance_lamports = lambda: next(seq)
    ex._order = lambda *_: {"transaction": "eA==", "requestId": "req", "outAmount": "10"}
    ex._simulate = lambda *_: {"err": None}
    monkeypatch.setattr(live_exec, "sign_versioned_transaction", lambda raw, key: b"signed")
    monkeypatch.setattr(live_exec.requests, "post", lambda *a, **k: _Response(payload))
    monkeypatch.setattr(sol, "settings", lambda app: {
        "live_require_execute_output": "true",
        "live_require_swap_events": "true",
    })
    return ex


def test_success_without_economic_output_is_post_execution_fault(monkeypatch):
    ex = _executor(monkeypatch, {
        "status": "Success",
        "code": 0,
        "signature": "landed-signature",
        "totalInputAmount": "1000",
        "totalOutputAmount": "0",
        "swapEvents": [],
    })
    with pytest.raises(live_exec.SolanaLivePostExecutionError) as caught:
        ex.swap("in", "out", 1000)
    result = caught.value.result
    assert caught.value.signature == "landed-signature"
    assert result["wallet_before_lamports"] == 10_000_000
    assert result["wallet_after_lamports"] == 9_000_000
    assert result["wallet_delta_lamports"] == -1_000_000


def test_successful_execution_carries_actual_wallet_delta(monkeypatch):
    ex = _executor(monkeypatch, {
        "status": "Success",
        "code": 0,
        "signature": "good-signature",
        "totalInputAmount": "1000",
        "totalOutputAmount": "900",
        "swapEvents": [{"type": "swap"}],
    }, balances=(20_000_000, 18_500_000))
    result = ex.swap("in", "out", 1000)
    assert result["wallet_delta_lamports"] == -1_500_000
    assert result["wallet_balance_reconciled"] is True


def _app(tmp_path):
    return SimpleNamespace(
        csv_dir=tmp_path / "CSVbot",
        data_dir=tmp_path / "data",
        telegram_bot_token="",
    )


def _event(signature):
    return {
        "leader_wallet": "leader",
        "signature": signature,
        "mint": "mint",
        "action": "BUY",
        "sol_amount": "0.001",
        "token_amount_raw": "100",
    }


def test_same_leader_signal_can_only_claim_one_chain_attempt(tmp_path):
    app = _app(tmp_path)
    first, key1 = live._claim_attempt(app, "123", _event("sig-1"))
    second, key2 = live._claim_attempt(app, "123", _event("sig-1"))
    assert first is True
    assert second is False
    assert key1 == key2


def test_second_landed_invalid_execution_disables_solana_live(tmp_path):
    app = _app(tmp_path)
    set_user_setting(app.csv_dir, "123", "solana_live_enabled", "true", chain_id=sol.SOLANA_CHAIN_ID)
    cfg = {"live_no_output_disable_after": "2"}

    for index in (1, 2):
        claimed, key = live._claim_attempt(app, "123", _event(f"sig-{index}"))
        assert claimed
        live._update_attempt(
            app, key, "LANDED_INVALID_OUTPUT",
            {
                "signature": f"tx-{index}",
                "totalInputAmount": "500000",
                "totalOutputAmount": "0",
                "wallet_before_lamports": "10000000",
                "wallet_after_lamports": "8155600",
                "wallet_delta_lamports": "-1844400",
            },
            "no output",
        )
        disabled = live._record_execution_fault(app, "123", cfg, "no output")
        if index == 1:
            assert disabled is False
            assert user_bool(app.csv_dir, "123", sol.SOLANA_CHAIN_ID, "solana_live_enabled", False) is True
        else:
            assert disabled is True
            assert user_bool(app.csv_dir, "123", sol.SOLANA_CHAIN_ID, "solana_live_enabled", True) is False


def test_live_position_uses_actual_wallet_spend_not_fixed_fee(tmp_path):
    app = _app(tmp_path)
    trade = {
        "signature": "tx",
        "totalInputAmount": "500000",
        "totalOutputAmount": "100",
        "wallet_delta_lamports": "-2344400",
    }
    pid, out_raw, entry_cost = live._insert_live_position(
        app, "123", 1, _event("leader-buy"), trade, Decimal("0.0005"),
        {"estimated_entry_fee_sol": "0.00002"},
    )
    assert pid
    assert out_raw == 100
    assert entry_cost == Decimal("0.0023444")
