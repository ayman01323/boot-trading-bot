from decimal import Decimal

from learnerbot import sibot1_gpt_solana_005_live_patch as patch
from learnerbot import sibot1_solana_live_bridge_patch as bridge


def test_gpt_entry_is_exactly_005_and_globals_restore(monkeypatch):
    original_default = bridge.DEFAULT_ENTRY_SOL
    original_hard_max = bridge.HARD_MAX_ENTRY_SOL
    seen = {}

    def fake_execute(app, tid, candidate, key):
        seen["size"] = bridge._entry_size({"max_sol_per_trade": "0.0005"})
        seen["default"] = bridge.DEFAULT_ENTRY_SOL
        seen["hard_max"] = bridge.HARD_MAX_ENTRY_SOL

    monkeypatch.setattr(patch, "_PREV_EXECUTE_ENTRY", fake_execute)
    patch._execute_entry_gpt_005(object(), "123", {"engine_id": "gpt"}, "key")

    assert seen["size"] == Decimal("0.005")
    assert seen["default"] == Decimal("0.005")
    assert seen["hard_max"] == Decimal("0.005")
    assert bridge.DEFAULT_ENTRY_SOL == original_default
    assert bridge.HARD_MAX_ENTRY_SOL == original_hard_max


def test_non_gpt_entry_keeps_existing_size(monkeypatch):
    seen = {}

    def fake_execute(app, tid, candidate, key):
        seen["size"] = bridge._entry_size({"max_sol_per_trade": "0.0005"})

    monkeypatch.setattr(patch, "_PREV_EXECUTE_ENTRY", fake_execute)
    patch._execute_entry_gpt_005(object(), "123", {"engine_id": "grok"}, "key")

    assert seen["size"] == Decimal("0.0005")
