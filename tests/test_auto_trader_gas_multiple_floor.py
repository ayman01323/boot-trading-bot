from decimal import Decimal

from learnerbot.auto_trader import _meets_gas_multiple_floor


def test_rejects_when_net_profit_barely_covers_gas():
    # Gross 0.00021, gas 0.0001 -> net over gas 0.00011, needs >= 2x gas (0.0002).
    sim = {"gross_profit": Decimal("0.00021"), "gas_cost_base": Decimal("0.0001")}
    assert _meets_gas_multiple_floor(sim, "2.0") is False


def test_accepts_when_net_profit_clears_the_multiple():
    # Gross 0.0004, gas 0.0001 -> net over gas 0.0003, needs >= 2x gas (0.0002).
    sim = {"gross_profit": Decimal("0.0004"), "gas_cost_base": Decimal("0.0001")}
    assert _meets_gas_multiple_floor(sim, "2.0") is True


def test_this_is_exactly_the_polygon_negligible_floor_scenario():
    # The flat 0.0002 min_net_profit_base floor is negligible on Polygon (WMATIC),
    # so a route can quote just barely above input+gas+0.0002 and still pass the
    # old flat floor while being economically worthless. The gas-multiple floor
    # catches this even though the flat floor alone would have passed it.
    gas_cost = Decimal("0.005")  # a realistic Polygon gas cost in WMATIC
    gross_profit = gas_cost + Decimal("0.0002") + Decimal("0.0001")  # clears the old flat floor
    sim = {"gross_profit": gross_profit, "gas_cost_base": gas_cost}
    assert _meets_gas_multiple_floor(sim, "2.0") is False


def test_zero_gas_cost_does_not_reject_defensively():
    sim = {"gross_profit": Decimal("0"), "gas_cost_base": Decimal("0")}
    assert _meets_gas_multiple_floor(sim, "2.0") is True


def test_multiplier_is_configurable():
    sim = {"gross_profit": Decimal("0.00025"), "gas_cost_base": Decimal("0.0001")}
    assert _meets_gas_multiple_floor(sim, "1.0") is True
    assert _meets_gas_multiple_floor(sim, "2.0") is False
