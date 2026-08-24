import json
from pathlib import Path
from types import SimpleNamespace

from scripts import urgent_strategy_factory_review as urgent


def _base_evidence():
    return {
        "operator_urgent_no_trade_report": False,
        "missing_target_strategies": [],
        "strategies_not_in_real_money_validation": [],
    }


def test_operator_urgent_always_escalates():
    evidence = _base_evidence()
    evidence["operator_urgent_no_trade_report"] = True
    assert urgent.needs_urgent_review(evidence) is True


def test_missing_target_strategy_escalates():
    evidence = _base_evidence()
    evidence["missing_target_strategies"] = ["Flow Acceleration"]
    assert urgent.needs_urgent_review(evidence) is True


def test_unpromoted_target_strategy_escalates():
    evidence = _base_evidence()
    evidence["strategies_not_in_real_money_validation"] = ["Cross Venue Net Arbitrage"]
    assert urgent.needs_urgent_review(evidence) is True


def test_no_escalation_after_all_targets_enter_real_money_validation():
    assert urgent.needs_urgent_review(_base_evidence()) is False


def test_watch_source_version_is_day_scoped_and_force_is_hour_scoped():
    a = urgent._finding_source_version(1787533200, force=True)
    b = urgent._finding_source_version(1787536799, force=True)
    assert a == b
    assert a.startswith("urgent-")

    c = urgent._finding_source_version(1787533200, force=False)
    d = urgent._finding_source_version(1787536800, force=False)
    assert c == d
    assert c.startswith("watch-")


def test_target_strategy_set_is_complete():
    assert set(urgent.TARGET_STRATEGIES) == {
        "Cross Venue Net Arbitrage",
        "Liquidity Confirmed Momentum",
        "Dislocation Mean Reversion",
        "Flow Acceleration",
        "New Liquidity Quality",
        "Learned Route Replication",
        "Forecasted Positive Net Edge",
    }


def test_bridge_target_rows_keep_all_strategies_shadow():
    rows = urgent._bridge_target_rows()
    assert [row["name"] for row in rows] == list(urgent.TARGET_STRATEGIES)
    assert all(row["status"] == "SHADOW" for row in rows)
    assert all(row["metrics"]["trades"] is None for row in rows)


def test_bridge_evidence_uses_sanitized_snapshots(monkeypatch, tmp_path: Path):
    paths = {}
    for index, name in enumerate(urgent.PRODUCTION_BRIDGES):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"generated_epoch": 100 + index, "marker": name}), encoding="utf-8")
        paths[name] = path
    monkeypatch.setattr(urgent, "PRODUCTION_BRIDGES", paths)

    evidence = urgent.build_bridge_evidence(now=200, operator_urgent=True)

    assert evidence["evidence_mode"] == "SANITISED_PRODUCTION_BRIDGES"
    assert evidence["operator_urgent_no_trade_report"] is True
    assert set(evidence["production_bridges"]) == set(paths)
    assert all(evidence["production_bridge_freshness"][name]["available"] for name in paths)
    assert evidence["strategies_not_in_real_money_validation"] == list(urgent.TARGET_STRATEGIES)
    assert evidence["known_architecture_boundaries"]["live_safety_bypass_allowed"] is False


def test_runner_review_storage_is_writable_and_separate_from_readonly_bridges(monkeypatch, tmp_path: Path):
    review_root = tmp_path / "review-state"
    monkeypatch.setattr(urgent, "RUNNER_REVIEW_ROOT", review_root)
    app = urgent._runner_app(SimpleNamespace(root=tmp_path))

    assert review_root.is_dir()
    assert Path(app.data_dir) == review_root
    assert Path(app.csv_dir) == urgent.BRIDGE_ROOT
