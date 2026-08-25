from decimal import Decimal

import pytest

from sibot1_engines._shared.capital import CapitalManager
from sibot1_engines._shared.positions import PositionManager


def test_one_wallet_virtual_allocations_cannot_double_spend():
    c = CapitalManager(Decimal("100"), {"gpt": Decimal("50"), "gemini": Decimal("50")})
    r = c.reserve("gpt", Decimal("40"))
    assert c.snapshot("gpt").cash == Decimal("10")
    with pytest.raises(ValueError):
        c.reserve("gpt", Decimal("11"))
    assert c.snapshot("gemini").cash == Decimal("50")
    c.release(r.reservation_id)
    assert c.snapshot("gpt").cash == Decimal("50")


def test_same_physical_token_is_separated_by_engine_lot():
    p = PositionManager()
    g = p.open_lot(engine_id="gpt", engine_version="1", strategy_id="a", chain="base", asset="ABC", quantity=Decimal("100"), cost_basis=Decimal("10"), entry_tx="0x1", entry_at_ms=1)
    m = p.open_lot(engine_id="gemini", engine_version="1", strategy_id="b", chain="base", asset="ABC", quantity=Decimal("60"), cost_basis=Decimal("6"), entry_tx="0x2", entry_at_ms=2)
    with pytest.raises(PermissionError):
        p.plan_exit(engine_id="gpt", lot_id=m.lot_id)
    x = p.plan_exit(engine_id="gpt", lot_id=g.lot_id, quantity=Decimal("100"))
    p.apply_exit(x)
    assert p.get(g.lot_id).remaining_quantity == 0
    assert p.get(m.lot_id).remaining_quantity == Decimal("60")


def test_emergency_exit_preserves_underlying_owner_attribution():
    p = PositionManager()
    p.open_lot(engine_id="gpt", engine_version="1", strategy_id="a", chain="base", asset="ABC", quantity=Decimal("5"), cost_basis=Decimal("1"), entry_tx="x", entry_at_ms=1)
    p.open_lot(engine_id="kimi", engine_version="1", strategy_id="b", chain="base", asset="ABC", quantity=Decimal("7"), cost_basis=Decimal("2"), entry_tx="y", entry_at_ms=2)
    rows = p.emergency_slices(chain="base", asset="ABC")
    assert {(r.engine_id, r.quantity) for r in rows} == {("gpt", Decimal("5")), ("kimi", Decimal("7"))}
