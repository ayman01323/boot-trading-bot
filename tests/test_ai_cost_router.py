from __future__ import annotations

from pathlib import Path

import pytest

from learnerbot import ai_cost_router as cost
from learnerbot import ai_cost_provider_patch as provider_patch
from learnerbot import master_change_cost_router_patch as cost_patch
from scripts import master_change_policy as policy


def _clean_cost_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI_COST_DB_PATH", str(tmp_path / "ai-cost.sqlite3"))
    monkeypatch.setenv("AI_COST_DAILY_BUDGET_USD", "10")
    monkeypatch.setenv("AI_COST_MONTHLY_BUDGET_USD", "100")
    monkeypatch.setenv("AI_COST_BUDGET_ENFORCE", "1")
    monkeypatch.setenv("AI_COST_TELEGRAM_ALERTS", "0")
    monkeypatch.setenv("AI_COST_WARNING_PERCENT", "80")
    for provider in ("GPT", "CLAUDE", "GEMINI", "DEEPSEEK", "COPILOT"):
        monkeypatch.delenv(f"AI_COST_{provider}_DAILY_BUDGET_USD", raising=False)
        monkeypatch.delenv(f"AI_COST_{provider}_MAX_DAILY_CALLS", raising=False)


def test_route_levels_are_deterministic_and_cost_escalating() -> None:
    mechanical = cost.route_request("read AI_AGENT_MESSAGING.md")
    assert mechanical["level"] == 0
    assert mechanical["advisers"] == []
    assert mechanical["model_calls_before_implementation"] == 0

    routine = cost.route_request("improve the wording in this documentation")
    assert routine["level"] == 1
    assert routine["advisers"] == ["deepseek"]

    normal = cost.route_request("fix the parser bug in the Telegram report")
    assert normal["level"] == 2
    assert normal["advisers"] == ["deepseek", "gemini"]

    important = cost.route_request("change the websocket architecture and service queue")
    assert important["level"] == 3
    assert important["advisers"] == ["gemini", "claude"]

    critical = cost.route_request("deploy new live trade execution risk logic")
    assert critical["level"] == 4
    assert critical["advisers"] == list(cost.ALL_ADVISERS)
    assert critical["gpt_model"] == "gpt-5.6-sol"


def test_master_change_never_uses_zero_ai_route() -> None:
    route = cost.master_change_route("read AI_AGENT_MESSAGING.md")
    assert route["level"] == 1
    assert route["advisers"] == ["deepseek"]
    assert route["gpt_model"] == "gpt-5.6-luna"
    assert route["model_calls_before_implementation"] == 2


def test_cost_ledger_records_per_provider_and_day(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clean_cost_env(monkeypatch, tmp_path)
    ticket = cost.reserve_call(
        "deepseek",
        "deepseek-v4-flash",
        "review this bounded change",
        max_output_tokens=1000,
        task_kind="test",
        route_level=1,
    )
    assert ticket.allowed
    assert ticket.estimated_usd > 0
    actual = cost.finish_call(ticket, success=True)
    assert actual == ticket.estimated_usd

    snap = cost.snapshot()
    assert snap["daily_usd"] > 0
    assert snap["monthly_usd"] > 0
    assert snap["by_provider_today"]["deepseek"]["calls"] == 1
    assert snap["by_provider_today"]["deepseek"]["usd"] > 0


def test_hard_budget_blocks_before_provider_spend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clean_cost_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_COST_DAILY_BUDGET_USD", "0.000001")
    ticket = cost.reserve_call(
        "gpt",
        "gpt-5.6-sol",
        "expensive final decision",
        max_output_tokens=2400,
        task_kind="test",
        route_level=4,
    )
    assert not ticket.allowed
    assert "daily budget" in ticket.reason.lower()
    assert cost.snapshot()["daily_usd"] == 0


def test_warning_threshold_is_one_shot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clean_cost_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_COST_DAILY_BUDGET_USD", "0.0009")
    monkeypatch.setenv("AI_COST_MONTHLY_BUDGET_USD", "10")
    monkeypatch.setenv("AI_COST_WARNING_PERCENT", "80")
    ticket = cost.reserve_call(
        "deepseek",
        "deepseek-v4-flash",
        "x" * 2000,
        max_output_tokens=2400,
        task_kind="test-warning",
        route_level=1,
    )
    assert ticket.allowed
    cost.finish_call(ticket, success=True)
    alerts = cost.pending_budget_alerts(mark=False)
    assert any(row["scope"] == "daily" and row["threshold"] == 80 for row in alerts)
    cost.mark_budget_alerts(alerts)
    assert cost.pending_budget_alerts(mark=False) == []


def test_provider_wrapper_refuses_call_when_budget_is_exhausted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clean_cost_env(monkeypatch, tmp_path)
    calls: list[tuple[str, str]] = []

    def fake_call(provider: str, prompt: str):
        calls.append((provider, prompt))
        return 0, "OK", ""

    monkeypatch.setattr(provider_patch._base, "call_provider", fake_call)
    monkeypatch.setenv("DEEPSEEK_COUNCIL_MODEL", "deepseek-v4-flash")
    rc, out, err = provider_patch.call_provider("deepseek", "first inexpensive review")
    assert (rc, out, err) == (0, "OK", "")
    assert len(calls) == 1

    monkeypatch.setenv("AI_COST_DAILY_BUDGET_USD", "0.000001")
    rc, out, err = provider_patch.call_provider("deepseek", "second review")
    assert rc == 95
    assert out == ""
    assert "Cost Router blocked" in err
    assert len(calls) == 1


def _schema2_evidence(request: str, *, protected: list[str] | None = None) -> dict:
    protected = protected or []
    route = cost.master_change_route(request, protected_reasons=protected)
    required = list(route["advisers"])
    return {
        "schema_version": 2,
        "request_id": "mc-20260823T090000Z-cost01",
        "request": request,
        "implementation_nonce": 1,
        "implementation_allowed": True,
        "hard_protected_reasons": [],
        "protected_reasons": protected,
        "all_advisers_replied": True,
        "source_sha": "a" * 40,
        "cost_route": route,
        "required_advisers": required,
        "advisers": {
            name: {"acknowledged": True, "provider_rc": 0, "reply": "APPROVE: bounded change"}
            for name in required
        },
        "gpt_decision": {
            "action": "IMPLEMENT",
            "risk_class": "LOW",
            "allowed_files": ["learnerbot/telegram_example_patch.py"],
            "auto_merge_recommended": False,
        },
    }


def test_policy_accepts_only_the_required_cost_routed_advisers() -> None:
    evidence = _schema2_evidence("improve documentation wording")
    assert evidence["required_advisers"] == ["deepseek"]
    allowed = policy.validate_request(
        evidence,
        request_id=evidence["request_id"],
        nonce=1,
        current_sha="a" * 40,
    )
    assert allowed == ["learnerbot/telegram_example_patch.py"]


def test_policy_recomputes_route_and_rejects_fake_downgrade() -> None:
    evidence = _schema2_evidence("deploy this live", protected=["deploy", "live"])
    assert evidence["cost_route"]["level"] == 4
    evidence["cost_route"] = cost.master_change_route("improve documentation wording")
    evidence["required_advisers"] = ["deepseek"]
    evidence["advisers"] = {
        "deepseek": {"acknowledged": True, "provider_rc": 0, "reply": "APPROVE"}
    }
    with pytest.raises(ValueError, match="cost route level mismatch"):
        policy.validate_request(
            evidence,
            request_id=evidence["request_id"],
            nonce=1,
            current_sha="a" * 40,
        )


def test_critical_route_requires_all_four_advisers() -> None:
    evidence = _schema2_evidence("change live trading execution risk limit", protected=["live", "risk limit"])
    assert evidence["required_advisers"] == list(cost.ALL_ADVISERS)
    del evidence["advisers"]["claude"]
    with pytest.raises(ValueError, match="claude required adviser"):
        policy.validate_request(
            evidence,
            request_id=evidence["request_id"],
            nonce=1,
            current_sha="a" * 40,
        )


def test_retry_reuse_helper_recognises_only_completed_adviser() -> None:
    assert cost_patch._successful({"acknowledged": True, "provider_rc": 0, "reply": "APPROVE"})
    assert not cost_patch._successful({"acknowledged": True, "provider_rc": 1, "reply": "failure"})
    assert not cost_patch._successful({"acknowledged": True, "provider_rc": 0, "reply": ""})
