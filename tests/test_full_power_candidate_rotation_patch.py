from __future__ import annotations

import learnerbot.full_power_candidate_rotation_patch as patch


def test_rotate_advances_without_changing_batch_size():
    patch._CURSOR.clear()
    items = list(range(12))
    assert patch._rotate(("test",), items, 3) == [0, 1, 2]
    assert patch._rotate(("test",), items, 3) == [3, 4, 5]
    assert patch._rotate(("test",), items, 3) == [6, 7, 8]


def test_graph_wrapper_explores_beyond_requested_prefix_without_more_quotes(monkeypatch):
    patch._CURSOR.clear()
    calls = []

    def fake_graph(pool_rows, chain_id, factory, wrapped, universe, max_checks):
        calls.append(max_checks)
        return list(range(max_checks))

    monkeypatch.setattr(patch, "_ORIGINAL_GRAPH_TRIANGLES", fake_graph)
    first = patch._rotating_graph_triangles([], 8453, "0xabc", "0xdef", [], 4)
    second = patch._rotating_graph_triangles([], 8453, "0xabc", "0xdef", [], 4)
    assert calls == [48, 48]
    assert len(first) == len(second) == 4
    assert first == [0, 1, 2, 3]
    assert second == [4, 5, 6, 7]


def test_v3_wrapper_rotates_by_chain_factory_without_changing_batch_size(monkeypatch):
    patch._CURSOR.clear()
    calls = []

    def fake_v3(pool_rows, wrapped, max_paths):
        calls.append(max_paths)
        return [(i, i) for i in range(max_paths)]

    monkeypatch.setattr(patch, "_ORIGINAL_V3_TRIANGLES", fake_v3)
    rows = [{"chain_id": 8453, "factory_address": "0xFactory"}]
    first = patch._rotating_v3_triangles(rows, "0xWrapped", 2)
    second = patch._rotating_v3_triangles(rows, "0xWrapped", 2)
    assert calls == [24, 24]
    assert len(first) == len(second) == 2
    assert first == [(0, 0), (1, 1)]
    assert second == [(2, 2), (3, 3)]


def test_install_does_not_patch_settings_or_quote_budget():
    # The patch must only replace graph candidate selection. The configured
    # fast_market_max_candidate_checks value remains owned by the existing settings.
    assert patch._fp._graph_triangles is patch._rotating_graph_triangles
    assert patch._fp._v3_triangles is patch._rotating_v3_triangles
    assert "_discovery_settings" not in patch.__dict__
