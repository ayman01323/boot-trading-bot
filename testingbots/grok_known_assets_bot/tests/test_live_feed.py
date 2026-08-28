from grok_known_assets_bot.core import Asset, Journal, RiskConfig
from grok_known_assets_bot.live_feed import LiveFeedSettings, SolanaNativeLiveFeed, _price_impact_bps, _route_pool_ids


def test_settings_are_bounded():
    cfg = LiveFeedSettings.from_raw({"paper_feed": {"poll_seconds": 1, "slippage_bps": 9999}})
    assert cfg.poll_seconds == 5.0
    assert cfg.slippage_bps == 500


def test_only_native_solana_sol_is_supported():
    assert SolanaNativeLiveFeed.supported(Asset("solana:SOL:NATIVE", "solana", "SOL", "NATIVE", True))
    assert not SolanaNativeLiveFeed.supported(Asset("base:ETH:NATIVE", "base", "ETH", "NATIVE", True))
    assert not SolanaNativeLiveFeed.supported(Asset("solana:WIF:x", "solana", "WIF", "x", True))


def test_route_helpers_collect_real_amm_keys_and_max_impact():
    q1 = {"priceImpactPct": "0.001", "routePlan": [{"swapInfo": {"ammKey": "A"}}]}
    q2 = {"priceImpactPct": "0.002", "routePlan": [{"swapInfo": {"ammKey": "B"}}, {"swapInfo": {"ammKey": "A"}}]}
    assert _route_pool_ids(q1, q2) == ("A", "B")
    assert _price_impact_bps(q1, q2) == 20.0


def test_history_persists_and_metrics(tmp_path):
    journal = Journal(tmp_path / "state.sqlite3")
    asset = Asset("solana:SOL:NATIVE", "solana", "SOL", "NATIVE", True)
    feed = SolanaNativeLiveFeed({asset.key: asset}, RiskConfig(), journal, {"paper_feed": {"poll_seconds": 5}})
    now = 10_000.0
    for age in range(900, -1, -60):
        feed._append_history(asset.key, now - age, 100.0 + (900 - age) / 900.0)
    rows = feed._load_history(asset.key)
    r1, r5, r15, vol = feed._metrics(rows, now, rows[-1][1])
    assert r15 > 0 and r5 > 0 and r1 > 0 and vol >= 0
