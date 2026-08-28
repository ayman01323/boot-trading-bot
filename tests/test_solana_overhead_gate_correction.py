from decimal import Decimal

from learnerbot import solana_overhead_gate_correction_patch as gate


def test_unreconciled_account_funding_does_not_block_valid_live_trade():
    cfg = {
        "live_min_economic_trade_sol": "0.009",
        "live_max_observed_overhead_pct": "35",
    }
    ok, reason = gate._economic_entry_gate_reconciled(
        object(), "123", Decimal("0.009"), cfg
    )
    assert ok is True
    assert reason == "ok"


def test_hard_economic_minimum_is_still_enforced():
    cfg = {"live_min_economic_trade_sol": "0.009"}
    ok, reason = gate._economic_entry_gate_reconciled(
        object(), "123", Decimal("0.008"), cfg
    )
    assert ok is False
    assert "below economic minimum" in reason
