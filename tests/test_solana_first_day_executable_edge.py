from decimal import Decimal
from types import SimpleNamespace

from learnerbot import solana_first_day_strategy_restore_patch as gate


def _base_cfg(**overrides):
    cfg = {
        "live_require_positive_executable_edge": "true",
        "live_executable_edge_min_samples": "5",
        "live_executable_edge_haircut_pct": "35",
        "live_executable_edge_latency_reserve_pct": "0.25",
        "live_min_executable_net_edge_pct": "0.25",
        "estimated_entry_fee_sol": "0.00002",
        "estimated_exit_fee_sol": "0.00002",
        "live_order_slippage_bps": "50",
    }
    cfg.update(overrides)
    return cfg


def _event():
    return {"action": "BUY", "leader_wallet": "leader", "mint": "mint"}


def test_inner_roundtrip_rejection_is_never_weakened(monkeypatch):
    expected_check = {"roundtrip_loss_pct": Decimal("4.0")}
    monkeypatch.setattr(
        gate,
        "_PREV_VALIDATE",
        lambda app, event, allocation, cfg: (False, "round-trip loss 4.000%", expected_check),
    )
    monkeypatch.setattr(
        gate,
        "_follower_edge_components",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("outer gate must not override inner rejection")),
    )

    ok, reason, check = gate.validate_entry_positive_executable_edge(
        SimpleNamespace(), _event(), Decimal("0.005"), _base_cfg()
    )
    assert not ok
    assert reason == "round-trip loss 4.000%"
    assert check is expected_check


def test_positive_cost_adjusted_follower_edge_passes(monkeypatch):
    monkeypatch.setattr(
        gate,
        "_PREV_VALIDATE",
        lambda app, event, allocation, cfg: (
            True,
            "PASS",
            {"roundtrip_loss_pct": Decimal("0.50"), "deterioration_pct": Decimal("0.10")},
        ),
    )
    monkeypatch.setattr(
        gate,
        "leader_expected_move_pct",
        lambda app, wallet, cfg: {
            "closed": 12,
            "recent_closed": 10,
            "minimum_samples": 5,
            "historical_mean_return_pct": Decimal("6"),
            "recent_mean_return_pct": Decimal("5"),
            "expected_move_pct": Decimal("5"),
        },
    )
    cfg = _base_cfg(
        live_executable_edge_haircut_pct="20",
        estimated_entry_fee_sol="0.00001",
        estimated_exit_fee_sol="0.00001",
        live_order_slippage_bps="20",
    )

    ok, reason, detail = gate.validate_entry_positive_executable_edge(
        SimpleNamespace(), _event(), Decimal("0.005"), cfg
    )
    assert ok
    assert reason == "PASS_POSITIVE_EXECUTABLE_EDGE"
    assert detail["executable_net_edge_pct"] > Decimal("0.25")
    assert detail["current_roundtrip_loss_pct"] == Decimal("0.50")


def test_costs_consuming_historical_move_rejects_buy(monkeypatch):
    monkeypatch.setattr(
        gate,
        "_PREV_VALIDATE",
        lambda app, event, allocation, cfg: (
            True,
            "PASS",
            {"roundtrip_loss_pct": Decimal("2.50"), "deterioration_pct": Decimal("0.50")},
        ),
    )
    monkeypatch.setattr(
        gate,
        "leader_expected_move_pct",
        lambda app, wallet, cfg: {
            "closed": 20,
            "recent_closed": 10,
            "minimum_samples": 5,
            "historical_mean_return_pct": Decimal("5"),
            "recent_mean_return_pct": Decimal("4"),
            "expected_move_pct": Decimal("4"),
        },
    )

    ok, reason, detail = gate.validate_entry_positive_executable_edge(
        SimpleNamespace(), _event(), Decimal("0.005"), _base_cfg()
    )
    assert not ok
    assert "positive executable edge rejected" in reason
    assert detail["executable_net_edge_pct"] < Decimal("0.25")
    assert detail["estimated_network_fee_pct"] == Decimal("0.8")


def test_insufficient_leader_samples_fail_closed(monkeypatch):
    monkeypatch.setattr(
        gate,
        "_PREV_VALIDATE",
        lambda app, event, allocation, cfg: (
            True,
            "PASS",
            {"roundtrip_loss_pct": Decimal("0.25")},
        ),
    )
    monkeypatch.setattr(
        gate,
        "leader_expected_move_pct",
        lambda app, wallet, cfg: {
            "closed": 2,
            "recent_closed": 2,
            "minimum_samples": 5,
            "historical_mean_return_pct": Decimal("8"),
            "recent_mean_return_pct": Decimal("8"),
            "expected_move_pct": Decimal("8"),
        },
    )

    ok, reason, detail = gate.validate_entry_positive_executable_edge(
        SimpleNamespace(), _event(), Decimal("0.005"), _base_cfg()
    )
    assert not ok
    assert "positive executable edge unavailable" in reason
    assert detail["closed"] == 2


def test_small_live_allocation_is_charged_for_network_costs(monkeypatch):
    monkeypatch.setattr(
        gate,
        "_PREV_VALIDATE",
        lambda app, event, allocation, cfg: (True, "PASS", {"roundtrip_loss_pct": Decimal("0.1")}),
    )
    monkeypatch.setattr(
        gate,
        "leader_expected_move_pct",
        lambda app, wallet, cfg: {
            "closed": 10,
            "recent_closed": 10,
            "minimum_samples": 5,
            "historical_mean_return_pct": Decimal("6"),
            "recent_mean_return_pct": Decimal("6"),
            "expected_move_pct": Decimal("6"),
        },
    )

    ok, reason, detail = gate.validate_entry_positive_executable_edge(
        SimpleNamespace(), _event(), Decimal("0.0005"), _base_cfg()
    )
    assert not ok
    # 0.00004 SOL estimated round-trip network cost is 8% of a 0.0005 SOL trade.
    assert detail["estimated_network_fee_pct"] == Decimal("8")
    assert "positive executable edge rejected" in reason


def test_restored_first_day_policy_remains_while_edge_gate_is_forced_on():
    targets = gate.FIRST_DAY_STRATEGY_TARGETS
    assert targets["leaders_per_user"] == "5"
    assert targets["max_signal_age_seconds"] == "30"
    assert targets["max_roundtrip_loss_pct"] == "3"
    assert targets["max_entry_deterioration_pct"] == "2"
    assert targets["stop_loss_pct"] == "10"
    assert targets["take_profit_pct"] == "25"
    assert targets["live_require_positive_executable_edge"] == "true"
    assert targets["live_min_executable_net_edge_pct"] == "0.25"
