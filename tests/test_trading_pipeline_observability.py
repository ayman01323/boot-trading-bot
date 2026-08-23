from __future__ import annotations

import pytest

from learnerbot import auto_trader
from learnerbot import strategy_factory_service_sender_patch as sender_patch
from learnerbot import trading_pipeline_observability_patch as obs


WALLET = "0x1111111111111111111111111111111111111111"
ROUTER = "0x2222222222222222222222222222222222222222"
AGGREGATOR = "0x3333333333333333333333333333333333333333"
TOKEN = "0x4444444444444444444444444444444444444444"
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"


def _normal(txh, to, ts, value=0):
    return {
        "hash": txh,
        "from": WALLET,
        "to": to,
        "timeStamp": str(ts),
        "value": str(value),
        "gasUsed": "21000",
        "gasPrice": "1000000000",
        "isError": "0",
        "txreceipt_status": "1",
    }


def _transfer(txh, token, frm, to, value, symbol="TOK", decimals=18):
    return {
        "hash": txh,
        "contractAddress": token,
        "from": frm,
        "to": to,
        "value": str(value),
        "tokenSymbol": symbol,
        "tokenDecimal": str(decimals),
    }


def _wrapped_round_trip(destination):
    buy = "0xaaa"
    sell = "0xbbb"
    amount = 10**18
    token_amount = 100 * 10**18
    normal = [
        _normal(buy, destination, 100, 0),
        _normal(sell, destination, 200, 0),
    ]
    token = [
        _transfer(buy, WBNB, WALLET, destination, amount, "WBNB"),
        _transfer(buy, TOKEN, destination, WALLET, token_amount, "TOK"),
        _transfer(sell, TOKEN, WALLET, destination, token_amount, "TOK"),
        _transfer(sell, WBNB, destination, WALLET, amount + amount // 10, "WBNB"),
    ]
    return normal, token, []


@pytest.mark.parametrize(
    ("router_kind", "destination"),
    [
        ("V2", "0x2222222222222222222222222222222222222222"),
        ("V3", "0x2626262626262626262626262626262626262626"),
        ("UNIVERSAL_ROUTER", "0x2727272727272727272727272727272727272727"),
    ],
)
def test_recognised_v2_v3_and_universal_router_round_trips_reconstruct(router_kind, destination):
    normal, token, internal = _wrapped_round_trip(destination)
    row = obs.reconstruction_diagnostic(
        WALLET,
        {destination},
        normal,
        token,
        internal,
        56,
        "bsc",
    )
    assert router_kind in {"V2", "V3", "UNIVERSAL_ROUTER"}
    assert row["router_txs"] == 2
    assert row["buys"] == 1
    assert row["sells"] == 1
    assert row["matched_closed"] == 1
    assert row["shadow_extra_matched_closed"] == 0


def test_known_router_reconstructs_live_history_pattern_without_shadow_extra():
    normal, token, internal = _wrapped_round_trip(ROUTER)
    row = obs.reconstruction_diagnostic(
        WALLET,
        {ROUTER},
        normal,
        token,
        internal,
        56,
        "bsc",
    )
    assert row["router_txs"] == 2
    assert row["buys"] == 1
    assert row["sells"] == 1
    assert row["matched_closed"] == 1
    assert row["shadow_extra_matched_closed"] == 0
    assert row["shadow_only"] is True


def test_unrecognised_aggregator_round_trip_is_shadow_only_not_live_history():
    normal, token, internal = _wrapped_round_trip(AGGREGATOR)
    row = obs.reconstruction_diagnostic(
        WALLET,
        {ROUTER},
        normal,
        token,
        internal,
        56,
        "bsc",
    )
    assert row["router_txs"] == 0
    assert row["buys"] == 0
    assert row["sells"] == 0
    assert row["matched_closed"] == 0
    assert row["shadow_unrecognised_buys"] == 1
    assert row["shadow_unrecognised_sells"] == 1
    assert row["shadow_all_routes_matched_closed"] == 1
    assert row["shadow_extra_matched_closed"] == 1
    assert "recognised router" in row["diagnostic_reason"]


def test_multitoken_flow_is_named_instead_of_silently_looking_successful():
    extra = "0x5555555555555555555555555555555555555555"
    normal, token, internal = _wrapped_round_trip(ROUTER)
    token.insert(
        2,
        _transfer("0xaaa", extra, ROUTER, WALLET, 5 * 10**18, "EXTRA"),
    )
    row = obs.reconstruction_diagnostic(
        WALLET,
        {ROUTER},
        normal,
        token,
        internal,
        56,
        "bsc",
    )
    assert row["matched_closed"] == 0
    assert row["buys"] == 0
    assert row["rejection_counts"]["recognised router multi-token/multi-hop flow not reconstructed"] >= 1
    assert row["diagnostic_reason"]


def test_observability_patch_does_not_replace_polygon_execution_function():
    # The patch may read auto_trader state/logs, but it must never interpose on the
    # capital-moving execution function merely to collect telemetry.
    assert auto_trader.execute_best_live_opportunity is obs._auto.execute_best_live_opportunity


def test_strategy_factory_service_sender_maps_only_to_master(monkeypatch):
    seen = {}

    async def fake(sender, target, body, **kwargs):
        seen.update(sender=sender, target=target, body=body, kwargs=kwargs)
        return {"status": "REPLIED"}

    monkeypatch.setattr(sender_patch, "_PREV_EXCHANGE", fake)
    import asyncio

    result = asyncio.run(sender_patch.exchange("strategy-factory", "gpt", "research", timeout=2))
    assert result["status"] == "REPLIED"
    assert seen["sender"] == "master"
    assert seen["target"] == "gpt"


def test_sol_selector_streak_is_monitoring_only(monkeypatch, tmp_path):
    path = tmp_path / "sol.json"
    monkeypatch.setattr(obs, "_SOL_SELECTOR_BRIDGE", path)
    monkeypatch.setattr(obs, "_SOL_ZERO_STREAK", 0)

    def base(pool, qualified, selected, failures, cfg):
        obs._atomic_json(path, {
            "pool": pool,
            "qualified": qualified,
            "selected": selected,
            "first_failure_counts": dict(failures),
            "thresholds_unchanged": True,
        })

    monkeypatch.setattr(obs, "_PREV_SOL_WRITE_BRIDGE", base)
    for _ in range(3):
        obs._sol_write_bridge_with_streak(45, 0, 0, {"historical win rate below minimum": 42}, {})
    result = obs._read_json(path)
    assert result["zero_qualified_streak"] == 3
    assert result["research_needed"] is True
    assert result["thresholds_unchanged"] is True

    obs._sol_write_bridge_with_streak(45, 1, 1, {}, {})
    result = obs._read_json(path)
    assert result["zero_qualified_streak"] == 0
    assert result["research_needed"] is False
