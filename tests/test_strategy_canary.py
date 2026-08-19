from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from learnerbot import strategy_canary as sc


def _app(tmp_path):
    data = tmp_path / "data"
    csv = tmp_path / "CSVbot"
    data.mkdir(); csv.mkdir()
    return SimpleNamespace(data_dir=data, csv_dir=csv, telegram_bot_token="")


def test_master_approval_requires_two_agents_confidence_and_shadow_only():
    status = {"three_agent_reports_complete": True, "master_decision_available": True}
    base = {
        "finding_id": "x",
        "action": "IMPROVE",
        "strategy": "Cross Venue Net Arbitrage",
        "disposition": "ACCEPT",
        "confidence": 0.91,
        "supporting_agents": ["gpt", "gemini"],
        "risk_class": "LOW",
        "shadow_only": True,
    }
    assert "cross venue net arbitrage" in sc._approval_rows({"decisions": [base]}, status)
    assert sc._approval_rows({"decisions": [{**base, "confidence": 0.7}]}, status) == {}
    assert sc._approval_rows({"decisions": [{**base, "supporting_agents": ["gpt"]}]}, status) == {}
    assert sc._approval_rows({"decisions": [{**base, "shadow_only": False}]}, status) == {}
    assert sc._approval_rows({"decisions": [{**base, "risk_class": "HIGH"}]}, status) == {}


def test_canary_promotes_to_probation_then_active_on_realised_results(tmp_path):
    app = _app(tmp_path)
    approval = {"cycle_id": "c1", "source_commit": "a" * 40}
    sc._state(app, "Cross Venue Net Arbitrage", approval, now=1)
    policy = {"strategy": "Cross Venue Net Arbitrage", **approval}
    r = None
    for n in ("0.001", "0.001", "0.001"):
        r = sc.record_canary_result(app, policy, realised_net_base=n, now=2)
    assert r["stage"] == "PROBATION"
    for _ in range(5):
        r = sc.record_canary_result(app, policy, realised_net_base="0.001", now=3)
    assert r["trades"] == 8
    assert r["stage"] == "ACTIVE"


def test_canary_pauses_after_two_consecutive_losses(tmp_path):
    app = _app(tmp_path)
    approval = {"cycle_id": "c1", "source_commit": "b" * 40}
    sc._state(app, "Learned Route Replication", approval, now=1)
    policy = {"strategy": "Learned Route Replication", **approval}
    sc.record_canary_result(app, policy, realised_net_base="0.001", now=2)
    sc.record_canary_result(app, policy, realised_net_base="-0.0002", now=3)
    r = sc.record_canary_result(app, policy, realised_net_base="-0.0002", now=4)
    assert r["stage"] == "PAUSED"


def test_canary_pauses_after_two_execution_failures(tmp_path):
    app = _app(tmp_path)
    approval = {"cycle_id": "c1", "source_commit": "c" * 40}
    sc._state(app, "Forecasted Positive Net Edge", approval, now=1)
    policy = {"strategy": "Forecasted Positive Net Edge", **approval}
    sc.record_canary_result(app, policy, execution_failure=True, reason="rpc", now=2)
    r = sc.record_canary_result(app, policy, execution_failure=True, reason="receipt", now=3)
    assert r["stage"] == "PAUSED"
