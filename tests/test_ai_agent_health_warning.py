from __future__ import annotations

from learnerbot.ai_agent_health_warning_patch import health_signature, unhealthy_rows, warning_message


def _snapshot(gpt="WORKING", gemini="NOT_WORKING", copilot="WORKING"):
    return {
        "engineering": {
            "available": True,
            "cycle": "abc123def456",
            "valid_count": 2,
            "master": "CONTINUING",
            "agents": {
                "gpt": {"state": gpt, "reason": "valid report"},
                "gemini": {"state": gemini, "reason": "API quota exceeded"},
                "copilot": {"state": copilot, "reason": "valid report"},
            },
        },
        "strategy": {"available": False, "agents": {}, "valid_count": 0, "master": "WAITING"},
        "checked_epoch": 1,
    }


def test_unhealthy_rows_only_include_not_working_agents():
    rows = unhealthy_rows(_snapshot())
    assert rows == [("engineering", "gemini", "API quota exceeded")]


def test_warning_lists_working_and_failed_agents_and_master_continuation():
    text = warning_message(_snapshot())
    assert "GPT: ✅ WORKING" in text
    assert "GEMINI: ⚠️ NOT_WORKING — API quota exceeded" in text
    assert "COPILOT: ✅ WORKING" in text
    assert "Master: continuing with 2/3 valid report(s)." in text
    assert "repeats every 30 minutes" in text


def test_health_signature_changes_when_failure_reason_changes():
    first = _snapshot()
    second = _snapshot()
    second["engineering"]["agents"]["gemini"]["reason"] = "credential missing"
    assert health_signature(first) != health_signature(second)


def test_waiting_agent_before_failure_grace_is_not_an_unhealthy_row():
    state = _snapshot(gemini="WAITING")
    assert unhealthy_rows(state) == []
