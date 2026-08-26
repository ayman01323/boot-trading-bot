from __future__ import annotations

import time
from types import SimpleNamespace

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


def test_weighted_budget_preserves_total_and_prioritises_base():
    ctxs = [
        SimpleNamespace(config=SimpleNamespace(slug=slug))
        for slug in ("base", "ethereum", "bsc", "arbitrum", "polygon")
    ]
    budgets = patch._weighted_budgets(ctxs, 60, base_share=0.40, minimum=5)
    assert sum(budgets.values()) == 60
    assert budgets["base"] >= 24
    assert all(value >= 5 for value in budgets.values())
    assert budgets["base"] > max(value for slug, value in budgets.items() if slug != "base")


def test_install_does_not_patch_settings_or_raise_quote_budget():
    assert patch._fp._graph_triangles is patch._rotating_graph_triangles
    assert patch._fp._v3_triangles is patch._rotating_v3_triangles
    assert patch._fp.scan_full_power_hot_routes is patch._scan_full_power_hot_routes
    assert "_discovery_settings" not in patch.__dict__


def test_base_result_uses_dedicated_feed_without_retimestamp_extra_scan_or_cross_dex(monkeypatch, tmp_path):
    app = SimpleNamespace(csv_dir=tmp_path)
    base = SimpleNamespace(config=SimpleNamespace(slug="base", chain_id=8453))
    eth = SimpleNamespace(config=SimpleNamespace(slug="ethereum", chain_id=1))
    observed = 1234567890
    base_row = {
        "chain_slug": "base",
        "observed_at_epoch": observed,
        "expected_gross_profit_base": "0.001",
        "slippage_reserve_base": "0.0001",
    }
    scan_calls = []
    writes = []

    monkeypatch.setattr(
        patch._fp,
        "load_kv_scoped",
        lambda path, chain_id: {
            "full_power_enabled": "true",
            "fast_market_max_candidate_checks": "10",
            "fast_market_max_routes_per_pass": "4",
            "full_power_parallel_chains": "2",
        },
    )

    def fake_v2(app_arg, ctx, settings, checks_budget, routes_budget):
        scan_calls.append((ctx.config.slug, "v2", checks_budget))
        if ctx.config.slug == "base":
            return [dict(base_row)], []
        time.sleep(0.05)
        return [], []

    def fake_v3(app_arg, ctx, settings, checks_budget, routes_budget):
        scan_calls.append((ctx.config.slug, "v3", checks_budget))
        return [], []

    def fake_cross(app_arg, ctx, settings, checks_budget):
        scan_calls.append((ctx.config.slug, "cross", checks_budget))
        return [], []

    monkeypatch.setattr(patch._fp, "_scan_v2_hot_chain", fake_v2)
    monkeypatch.setattr(patch._fp, "_scan_v3_chain", fake_v3)
    monkeypatch.setattr(patch._fp, "_scan_cross_v2_chain", fake_cross)
    monkeypatch.setattr(
        patch._fp,
        "_atomic_write",
        lambda path, rows, headers: writes.append((path.name, [dict(r) for r in rows])),
    )
    monkeypatch.setattr(patch._fp, "_atomic_rows", lambda path, rows, headers: None)

    _, rows, _ = patch._scan_full_power_hot_routes(app, [base, eth])

    assert sum(1 for call in scan_calls if call[0] == "base" and call[1] == "v2") == 1
    assert sum(1 for call in scan_calls if call[0] == "ethereum" and call[1] == "v2") == 1
    assert not any(call[0] == "base" and call[1] == "cross" for call in scan_calls)
    # First write is the durable Base-only feed; final combined write remains separate.
    assert writes[0][0] == "base_full_power_opportunities.csv"
    assert writes[0][1] == [base_row]
    assert writes[0][1][0]["observed_at_epoch"] == observed
    assert writes[-1][0] == "full_power_opportunities.csv"
    assert writes[-1][1] == [base_row]
    assert rows == [base_row]
