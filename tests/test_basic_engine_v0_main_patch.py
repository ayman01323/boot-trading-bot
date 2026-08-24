from __future__ import annotations

import pytest


def test_basic_v0_is_bound_as_main_entrypoint():
    from learnerbot.basic_engine_v0 import main_patch as patch

    assert patch._auto.execute_best_live_opportunity is patch.execute_best_live_opportunity_v0
    assert patch._fast.execute_best_live_opportunity is patch.execute_best_live_opportunity_v0
    assert patch._cli.execute_best_live_opportunity is patch.execute_best_live_opportunity_v0


def test_basic_v0_bypasses_only_two_starvation_gates_and_restores(monkeypatch):
    from learnerbot.basic_engine_v0 import main_patch as patch

    original_product = patch._auto.route_product_policy
    original_gas = patch._auto._meets_gas_multiple_floor
    seen = {}

    def fake_legacy(app, opportunities):
        seen["product"] = patch._auto.route_product_policy(None, 8453, ["a", "b", "a"])
        seen["gas"] = patch._auto._meets_gas_multiple_floor({"gas_cost_base": "1"}, "99")
        seen["opportunities"] = opportunities
        return [{"status": "TEST_ONLY"}]

    monkeypatch.setattr(patch, "_LEGACY_EXECUTE", fake_legacy)
    rows = [{"route_id": "r1"}]
    result = patch.execute_best_live_opportunity_v0(object(), rows)

    assert result == [{"status": "TEST_ONLY"}]
    assert seen["product"]["auto_trade"] is True
    assert seen["gas"] is True
    assert seen["opportunities"] is rows
    assert patch._auto.route_product_policy is original_product
    assert patch._auto._meets_gas_multiple_floor is original_gas


def test_basic_v0_restores_gates_after_exception(monkeypatch):
    from learnerbot.basic_engine_v0 import main_patch as patch

    original_product = patch._auto.route_product_policy
    original_gas = patch._auto._meets_gas_multiple_floor

    def boom(app, opportunities):
        raise RuntimeError("boom")

    monkeypatch.setattr(patch, "_LEGACY_EXECUTE", boom)
    with pytest.raises(RuntimeError, match="boom"):
        patch.execute_best_live_opportunity_v0(object(), [])

    assert patch._auto.route_product_policy is original_product
    assert patch._auto._meets_gas_multiple_floor is original_gas
