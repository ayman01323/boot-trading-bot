from __future__ import annotations

from types import SimpleNamespace


def test_basic_v0_is_bound_before_candidate_generation():
    from learnerbot.basic_engine_v0 import main_patch as patch

    assert patch._full.route_product_policy is patch._v0_scanner_product_policy
    assert patch._full.allowed_product_addresses is patch._v0_scanner_allowed_products
    assert getattr(patch._full, "_basic_engine_v0_scanner_patch_installed", False) is True


def test_scanner_admission_bypass_preserves_risk_metadata(monkeypatch):
    from learnerbot.basic_engine_v0 import main_patch as patch

    monkeypatch.setattr(
        patch,
        "_ORIGINAL_SCAN_PRODUCT_POLICY",
        lambda *_args, **_kwargs: {
            "auto_trade": False,
            "risk_level": 3,
            "category": "DISCOVERED_SCAN_ONLY",
            "reason": "only one verified pool",
        },
    )

    policy = patch._v0_scanner_product_policy(None, 56, ["wrapped", "token", "wrapped"])

    assert policy["auto_trade"] is True
    assert policy["risk_level"] == 3
    assert policy["category"] == "DISCOVERED_SCAN_ONLY"
    assert "prior=only one verified pool" in policy["reason"]


def test_configured_seed_survives_empty_dynamic_product_universe(monkeypatch):
    from learnerbot.basic_engine_v0 import main_patch as patch

    seed = "0x0000000000000000000000000000000000000001"
    dynamic = "0x0000000000000000000000000000000000000002"
    monkeypatch.setattr(patch, "_ORIGINAL_SCAN_ALLOWED_PRODUCTS", lambda *_a, **_k: [])
    monkeypatch.setattr(patch._full, "_configured_token_seeds", lambda *_a, **_k: [seed])

    assert patch._v0_scanner_allowed_products(None, 56, include_shadow=True) == [seed]

    monkeypatch.setattr(patch, "_ORIGINAL_SCAN_ALLOWED_PRODUCTS", lambda *_a, **_k: [dynamic, seed])
    assert patch._v0_scanner_allowed_products(None, 56, include_shadow=True) == [seed, dynamic]


def test_v2_discovery_runs_when_v3_is_disabled(monkeypatch, tmp_path):
    from learnerbot.basic_engine_v0 import scanner_patch as patch

    app = SimpleNamespace(csv_dir=tmp_path)
    ctx = SimpleNamespace(config=SimpleNamespace(chain_id=56, slug="bsc"))
    venue = {
        "router": "0x0000000000000000000000000000000000000010",
        "factory": "0x0000000000000000000000000000000000000020",
        "dex_name": "TEST_V2",
    }
    calls = {"crawl": 0, "seed": 0, "v3": 0}

    monkeypatch.setattr(patch._full, "load_kv_scoped", lambda *_a, **_k: {})
    monkeypatch.setattr(
        patch._full,
        "_venues",
        lambda _app, _cid, version: [venue] if version == "V2" else [],
    )
    monkeypatch.setattr(patch._full, "_rows", lambda *_a, **_k: [])

    class FakeTrader:
        def __init__(self, *_a, **_k):
            pass

    monkeypatch.setattr(patch, "LiveTrader", FakeTrader)

    def crawl(*_a, **_k):
        calls["crawl"] += 1
        return []

    def seed(*_a, **_k):
        calls["seed"] += 1
        return [{"pair": "verified"}]

    def v3(*_a, **_k):
        calls["v3"] += 1
        raise AssertionError("V3 discovery must not run when include_v3=False")

    monkeypatch.setattr(patch._full, "_crawl_factory_pairs", crawl)
    monkeypatch.setattr(patch._full, "_seed_factory_pairs", seed)
    monkeypatch.setattr(patch._full, "discover_v3_seed_pools_for_context", v3)

    result = patch.discover_full_power_pools_v0(app, [ctx], include_v3=False)

    assert calls == {"crawl": 1, "seed": 1, "v3": 0}
    assert result["v2_pools_added"] == 1
    assert result["v3_pools_seen"] == 0
    assert result["rejected"] == 0


def test_v3_hot_scanner_honours_v3_switch(monkeypatch):
    from learnerbot.basic_engine_v0 import scanner_patch as patch

    called = []
    monkeypatch.setattr(
        patch,
        "_ORIGINAL_SCAN_V3",
        lambda *args: called.append(args) or ([{"route_id": "v3"}], []),
    )

    assert patch.scan_v3_chain_v0(object(), object(), {"v3_scanner_enabled": "false"}, 10, 5) == ([], [])
    assert called == []

    rows, rejected = patch.scan_v3_chain_v0(
        object(), object(), {"v3_scanner_enabled": "true"}, 10, 5
    )
    assert rows == [{"route_id": "v3"}]
    assert rejected == []
    assert len(called) == 1
