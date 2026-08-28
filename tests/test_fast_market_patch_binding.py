from __future__ import annotations

from types import SimpleNamespace

import learnerbot.fast_market as fast_market
import learnerbot.full_power_scanner as full_power_scanner


def test_run_fast_market_pass_resolves_installed_scanner_at_call_time(monkeypatch, tmp_path):
    """The combined pass must not retain the pre-patch scanner function object."""
    app = SimpleNamespace(csv_dir=tmp_path, telegram_bot_token="")
    ctx = SimpleNamespace(config=SimpleNamespace(slug="base", chain_id=8453))
    calls = []

    def fake_scan(app_arg, ctxs):
        calls.append((app_arg, list(ctxs)))
        return tmp_path / "full_power_opportunities.csv", [{"enabled": "false"}], []

    monkeypatch.setattr(full_power_scanner, "scan_full_power_hot_routes", fake_scan)
    monkeypatch.setattr(fast_market, "contexts", lambda *args, **kwargs: [ctx])
    monkeypatch.setattr(fast_market, "_rows", lambda path: [])
    monkeypatch.setattr(
        fast_market,
        "merge_live_opportunities",
        lambda app_arg, learned_rows, market_rows: (
            tmp_path / "direct_market_opportunities.csv",
            list(market_rows),
        ),
    )
    monkeypatch.setattr(fast_market, "execute_best_live_opportunity", lambda app_arg, rows: [])
    monkeypatch.setattr(fast_market, "_write_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(fast_market, "close_contexts", lambda ctxs: None)

    result = fast_market.run_fast_market_pass(app)

    assert calls == [(app, [ctx])]
    assert result["routes"] == 1
    assert result["merged_routes"] == 1
