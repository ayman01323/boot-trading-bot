from types import SimpleNamespace

import pytest

from learnerbot import solana_execution_validation_patch as guard
from learnerbot import solana_live_executor as execmod


def _post_error(message, **result):
    base = {
        "status": "Success",
        "code": 0,
        "signature": "sig",
        "totalInputAmount": "500000",
        "totalOutputAmount": "123",
    }
    base.update(result)
    return execmod.SolanaLivePostExecutionError(message, base)


def test_missing_swap_events_only_is_recoverable():
    exc = _post_error(
        "Jupiter transaction reported Success but failed economic validation: missing swapEvents"
    )
    assert guard._only_missing_swap_events(exc) is True


def test_missing_events_with_zero_output_is_not_recoverable():
    exc = _post_error(
        "Jupiter transaction reported Success but failed economic validation: non-positive executed output, missing swapEvents",
        totalOutputAmount="0",
    )
    assert guard._only_missing_swap_events(exc) is False


def test_swap_returns_positive_amount_result_when_only_events_are_missing(monkeypatch):
    def old_swap(self, *_):
        raise _post_error(
            "Jupiter transaction reported Success but failed economic validation: missing swapEvents"
        )

    monkeypatch.setattr(guard, "_PREV_SWAP", old_swap)
    result = guard._swap_amounts_authoritative(SimpleNamespace(), "in", "out", 500000)
    assert result["signature"] == "sig"
    assert result["economic_validation"] == "POSITIVE_AMOUNTS_EVENTS_MISSING"
    assert result["swap_events_present"] is False


def test_buy_requires_actual_output_token_increase_when_reconciled(monkeypatch):
    balances = iter([10, 10])
    fake = SimpleNamespace(token_balance_raw=lambda mint: next(balances))
    monkeypatch.setattr(
        guard,
        "_PREV_BUY",
        lambda self, mint, amount, reserve: {
            "status": "Success",
            "code": 0,
            "signature": "buy-sig",
            "totalInputAmount": "500000",
            "totalOutputAmount": "100",
        },
    )
    with pytest.raises(execmod.SolanaLivePostExecutionError) as caught:
        guard._buy_with_token_reconciliation(fake, "mint", 0.0005, 0.005)
    assert "no output-token balance increase" in str(caught.value)


def test_sell_requires_actual_input_token_decrease_when_reconciled(monkeypatch):
    balances = iter([100, 100])
    fake = SimpleNamespace(token_balance_raw=lambda mint: next(balances))
    monkeypatch.setattr(
        guard,
        "_PREV_SELL",
        lambda self, mint, amount: {
            "status": "Success",
            "code": 0,
            "signature": "sell-sig",
            "totalInputAmount": "50",
            "totalOutputAmount": "250000",
        },
    )
    with pytest.raises(execmod.SolanaLivePostExecutionError) as caught:
        guard._sell_with_token_reconciliation(fake, "mint", 50)
    assert "no input-token balance decrease" in str(caught.value)
