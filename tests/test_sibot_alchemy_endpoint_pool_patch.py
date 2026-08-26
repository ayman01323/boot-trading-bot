from __future__ import annotations

import csv
from types import SimpleNamespace

import learnerbot.sibot_alchemy_endpoint_pool_patch as patch


def _write_rpc_csv(tmp_path):
    path = tmp_path / "rpc_endpoints.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["chain_id", "url", "priority", "enabled"])
        writer.writeheader()
        writer.writerow({"chain_id": 8453, "url": "https://second.alchemy.com/v2/secret-b", "priority": 20, "enabled": "true"})
        writer.writerow({"chain_id": 8453, "url": "https://first.alchemy.com/v2/secret-a", "priority": 10, "enabled": "true"})
        writer.writerow({"chain_id": 8453, "url": "https://example-rpc.invalid", "priority": 1, "enabled": "true"})
        writer.writerow({"chain_id": 1, "url": "https://eth.alchemy.com/v2/other", "priority": 1, "enabled": "true"})
    return path


def _reset_pool():
    patch._TLS.forced_url = ""
    with patch._STATE_LOCK:
        patch._COOLDOWN_UNTIL.clear()
        patch._PRESSURE_COUNT.clear()
        patch._LAST_SUCCESS.clear()


def test_endpoint_pool_orders_enabled_alchemy_urls_and_health_is_redacted(tmp_path):
    _write_rpc_csv(tmp_path)
    _reset_pool()
    app = SimpleNamespace(csv_dir=tmp_path)
    urls = patch.alchemy_rpc_urls(app, 8453)
    assert urls == [
        "https://first.alchemy.com/v2/secret-a",
        "https://second.alchemy.com/v2/secret-b",
    ]
    health = patch.endpoint_pool_health(app, 8453)
    assert health == {
        "configured": 2,
        "available_now": 2,
        "cooling": 0,
        "has_preferred_success": False,
        "identifiers_redacted": True,
    }
    text = repr(health)
    assert "secret-a" not in text
    assert "secret-b" not in text


def test_rate_limit_fails_over_once_to_distinct_endpoint(monkeypatch, tmp_path):
    _write_rpc_csv(tmp_path)
    _reset_pool()
    app = SimpleNamespace(csv_dir=tmp_path)
    chain = SimpleNamespace(chain_id=8453, slug="base")
    calls = []

    def fake_refresh(app_arg, chain_arg, wallet):
        forced = str(getattr(patch._TLS, "forced_url", ""))
        calls.append(forced)
        if "first.alchemy.com" in forced:
            return {
                "wallet": wallet,
                "complete": False,
                "error": "AlchemyHistoryError: RuntimeError: Alchemy alchemy_getAssetTransfers: HTTP 429",
            }
        return {"wallet": wallet, "complete": True, "trades": 2, "error": ""}

    monkeypatch.setattr(patch, "_PREV_REFRESH", fake_refresh)
    result = patch.refresh_wallet_history_with_endpoint_pool(app, chain, "0xabc")

    assert len(calls) == 2
    assert "first.alchemy.com" in calls[0]
    assert "second.alchemy.com" in calls[1]
    assert result["complete"] is True
    assert result["endpoint_failover_attempts"] == 2
    assert result["endpoint_identifiers_redacted"] is True
    health = patch.endpoint_pool_health(app, 8453)
    assert health["configured"] == 2
    assert health["cooling"] == 1
    assert health["available_now"] == 1
    assert health["has_preferred_success"] is True


def test_progress_yield_does_not_switch_endpoint(monkeypatch, tmp_path):
    _write_rpc_csv(tmp_path)
    _reset_pool()
    app = SimpleNamespace(csv_dir=tmp_path)
    chain = SimpleNamespace(chain_id=8453, slug="base")
    calls = []

    def fake_refresh(app_arg, chain_arg, wallet):
        calls.append(str(getattr(patch._TLS, "forced_url", "")))
        return {
            "wallet": wallet,
            "complete": False,
            "error": "AlchemyHistoryProgress: context progress pending 30/100; worker yielded for cross-chain fairness",
        }

    monkeypatch.setattr(patch, "_PREV_REFRESH", fake_refresh)
    result = patch.refresh_wallet_history_with_endpoint_pool(app, chain, "0xabc")
    assert len(calls) == 1
    assert result["error"].startswith("AlchemyHistoryProgress:")
    assert patch.endpoint_pool_health(app, 8453)["cooling"] == 0
