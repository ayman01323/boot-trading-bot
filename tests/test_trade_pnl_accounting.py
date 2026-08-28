from decimal import Decimal

from learnerbot.trade_pnl_accounting import entry_cash_cost, exit_accounting


def test_entry_cash_cost_uses_actual_wallet_delta():
    assert entry_cash_cost("1.000000000", "0.994800000") == Decimal("0.005200000")
    assert entry_cash_cost("1", "1") is None
    assert entry_cash_cost(None, "1") is None


def test_full_exit_net_pnl_after_cost_basis():
    result = exit_accounting(
        remaining_cost_native="0.0052",
        realised_net_native="0",
        realised_cost_native="0",
        sold_raw=1000,
        position_raw_before=1000,
        wallet_cash_change_native="0.0061",
    )
    assert result is not None
    assert result["cost_basis_native"] == Decimal("0.0052")
    assert result["net_this_native"] == Decimal("0.0009")
    assert result["realised_net_total_native"] == Decimal("0.0009")
    assert result["remaining_cost_native"] == Decimal("0")
    assert result["net_pct_this"].quantize(Decimal("0.01")) == Decimal("17.31")


def test_partial_exits_allocate_remaining_cost_and_accumulate_realised_net():
    first = exit_accounting(
        remaining_cost_native="0.010",
        realised_net_native="0",
        realised_cost_native="0",
        sold_raw=400,
        position_raw_before=1000,
        wallet_cash_change_native="0.0048",
    )
    assert first is not None
    assert first["cost_basis_native"] == Decimal("0.0040")
    assert first["net_this_native"] == Decimal("0.0008")
    assert first["remaining_cost_native"] == Decimal("0.0060")

    second = exit_accounting(
        remaining_cost_native=first["remaining_cost_native"],
        realised_net_native=first["realised_net_total_native"],
        realised_cost_native=first["realised_cost_total_native"],
        sold_raw=600,
        position_raw_before=600,
        wallet_cash_change_native="0.0054",
    )
    assert second is not None
    assert second["cost_basis_native"] == Decimal("0.0060")
    assert second["net_this_native"] == Decimal("-0.0006")
    assert second["realised_net_total_native"] == Decimal("0.0002")
    assert second["realised_cost_total_native"] == Decimal("0.0100")
    assert second["net_pct_total"].quantize(Decimal("0.01")) == Decimal("2.00")


def test_exit_accounting_refuses_unproven_inputs():
    assert exit_accounting(
        remaining_cost_native=None,
        realised_net_native="0",
        realised_cost_native="0",
        sold_raw=100,
        position_raw_before=100,
        wallet_cash_change_native="0.1",
    ) is None
    assert exit_accounting(
        remaining_cost_native="1",
        realised_net_native="0",
        realised_cost_native="0",
        sold_raw=0,
        position_raw_before=100,
        wallet_cash_change_native="0.1",
    ) is None
