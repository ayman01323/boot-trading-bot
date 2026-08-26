from __future__ import annotations

import json
from types import SimpleNamespace

import learnerbot.sibot_history_router_learning_patch as patch


def test_router_promotion_requires_two_wallets_and_three_closed_matches(tmp_path):
    patch._PROMOTED.clear()
    app = SimpleNamespace(data_dir=tmp_path)
    destination = "0x" + "a" * 40
    wallet1 = "0x" + "1" * 40
    wallet2 = "0x" + "2" * 40

    assert patch._record_destination_evidence(app, 8453, destination, wallet1, 3, now_epoch=100) is False
    assert destination not in patch._PROMOTED.get(8453, set())

    assert patch._record_destination_evidence(app, 8453, destination, wallet2, 1, now_epoch=101) is True
    assert destination in patch._PROMOTED.get(8453, set())

    raw = (tmp_path / patch._STATE_FILE).read_text(encoding="utf-8")
    state = json.loads(raw)
    evidence = state["chains"]["8453"]["destinations"][destination]
    assert evidence["independent_wallets"] == 2
    assert evidence["closed_matches"] == 4
    assert evidence["promoted"] is True
    assert wallet1.lower() not in raw.lower()
    assert wallet2.lower() not in raw.lower()
    assert state["history_only"] is True
    assert state["execution_router_registry_changed"] is False


def test_reconstructor_adds_promoted_routes_only_when_router_gate_is_active(monkeypatch):
    destination = "0x" + "a" * 40
    configured = "0x" + "b" * 40
    captured = []
    patch._PROMOTED.clear()
    patch._PROMOTED[8453] = {destination}

    def fake_reconstruct(wallet, routers, normal, token, internal, chain_id, chain_slug):
        captured.append(set(routers))
        return [], 0

    monkeypatch.setattr(patch, "_PREV_RECONSTRUCT", fake_reconstruct)

    patch.reconstruct_spot_trades_with_history_routers(
        "0x" + "1" * 40, {configured}, [], [], [], 8453, "base"
    )
    patch.reconstruct_spot_trades_with_history_routers(
        "0x" + "1" * 40, set(), [], [], [], 8453, "base"
    )

    assert captured[0] == {configured, destination}
    # Empty set is the observability layer's deliberate all-destination SHADOW replay.
    assert captured[1] == set()


def test_learning_uses_strict_existing_reconstructor_before_recording(monkeypatch, tmp_path):
    patch._PROMOTED.clear()
    app = SimpleNamespace(data_dir=tmp_path)
    chain = SimpleNamespace(chain_id=8453, slug="base")
    wallet = "0x" + "1" * 40
    destination = "0x" + "a" * 40
    normal = [{
        "hash": "0xabc",
        "from": wallet,
        "to": destination,
        "isError": "0",
        "txreceipt_status": "1",
    }]
    calls = []

    monkeypatch.setattr(patch._sibot, "_routers", lambda app_arg, chain_arg: {"0x" + "b" * 40})

    def fake_reconstruct(wallet_arg, routers, normal_arg, token_arg, internal_arg, chain_id, chain_slug):
        calls.append(set(routers))
        return ([{"trade_id": "closed"}], 0) if destination in routers else ([], 0)

    monkeypatch.setattr(patch, "_PREV_RECONSTRUCT", fake_reconstruct)
    result = patch._learn_from_history(app, chain, wallet, normal, [], [])

    assert calls == [{destination}]
    assert result["evidenced_destinations"] == 1
    assert result["newly_promoted"] == 0
    assert destination not in patch._PROMOTED.get(8453, set())
