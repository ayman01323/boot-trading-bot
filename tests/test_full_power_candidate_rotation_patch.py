from __future__ import annotations

from pathlib import Path

import learnerbot.full_power_candidate_rotation_patch as patch


def test_rotate_advances_without_changing_batch_size():
    patch._CURSOR.clear()
    items = list(range(12))
    assert patch._rotate(("test",), items, 3) == [0, 1, 2]
    assert patch._rotate(("test",), items, 3) == [3, 4, 5]
    assert patch._rotate(("test",), items, 3) == [6, 7, 8]


def test_graph_wrapper_explores_beyond_requested_prefix(monkeypatch):
    patch._CURSOR.clear()
    calls = []

    def fake_graph(pool_rows, chain_id, factory, wrapped, universe, max_checks):
        calls.append(max_checks)
        return list(range(max_checks))

    monkeypatch.setattr(patch, "_ORIGINAL_GRAPH_TRIANGLES", fake_graph)
    first = patch._rotating_graph_triangles([], 8453, "0xabc", "0xdef", [], 4)
    second = patch._rotating_graph_triangles([], 8453, "0xabc", "0xdef", [], 4)
    assert calls == [48, 48]
    assert first == [0, 1, 2, 3]
    assert second == [4, 5, 6, 7]


def test_v3_wrapper_rotates_by_chain_factory(monkeypatch):
    patch._CURSOR.clear()

    def fake_v3(pool_rows, wrapped, max_paths):
        return [(i, i) for i in range(max_paths)]

    monkeypatch.setattr(patch, "_ORIGINAL_V3_TRIANGLES", fake_v3)
    rows = [{"chain_id": 8453, "factory_address": "0xFactory"}]
    first = patch._rotating_v3_triangles(rows, "0xWrapped", 2)
    second = patch._rotating_v3_triangles(rows, "0xWrapped", 2)
    assert first == [(0, 0), (1, 1)]
    assert second == [(2, 2), (3, 3)]


def test_candidate_budget_has_floor_but_preserves_higher_config(monkeypatch):
    monkeypatch.setattr(
        patch,
        "_ORIGINAL_LOAD_KV_SCOPED",
        lambda path, chain_id: {"fast_market_max_candidate_checks": "60", "x": "y"},
    )
    result = patch._discovery_settings(Path("CSVbot/auto_trading_settings.csv"), 0)
    assert result["fast_market_max_candidate_checks"] == "120"
    assert result["x"] == "y"

    monkeypatch.setattr(
        patch,
        "_ORIGINAL_LOAD_KV_SCOPED",
        lambda path, chain_id: {"fast_market_max_candidate_checks": "240"},
    )
    result = patch._discovery_settings(Path("CSVbot/auto_trading_settings.csv"), 0)
    assert result["fast_market_max_candidate_checks"] == "240"


def test_non_auto_settings_are_untouched(monkeypatch):
    original = {"fast_market_max_candidate_checks": "12"}
    monkeypatch.setattr(patch, "_ORIGINAL_LOAD_KV_SCOPED", lambda path, chain_id: original)
    assert patch._discovery_settings(Path("CSVbot/risk_settings.csv"), 8453) is original
