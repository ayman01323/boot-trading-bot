from learnerbot import ai_health_compact_report_patch as compact
from learnerbot import kimi_ai_health_roster_patch as kimi_health


def _ensure_kimi_roster():
    kimi_health.install()


def _health(*states):
    _ensure_kimi_roster()
    providers = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")
    return {
        "agents": {
            provider: {"state": state, "reason": state}
            for provider, state in zip(providers, states)
        }
    }


def test_master_dashboard_keeps_sections_but_does_not_repeat_every_agent_per_lane():
    _ensure_kimi_roster()
    engineering = {
        "agents": {
            "gpt": {"state": "WORKING", "reason": ""},
            "claude": {"state": "NOT_WORKING", "reason": "pipeline failed"},
            "gemini": {"state": "WORKING", "reason": ""},
            "deepseek": {"state": "NOT_WORKING", "reason": "unsupported model config"},
            "grok": {"state": "WORKING", "reason": ""},
            "kimi": {"state": "WORKING", "reason": ""},
            "copilot": {"state": "WAITING", "reason": "in progress"},
        }
    }
    strategy = {
        "agents": {
            "gpt": {"state": "WORKING", "reason": ""},
            "claude": {"state": "WORKING", "reason": ""},
            "gemini": {"state": "WORKING", "reason": ""},
            "deepseek": {"state": "NOT_WORKING", "reason": "unsupported model config"},
            "grok": {"state": "WORKING", "reason": ""},
            "kimi": {"state": "WORKING", "reason": ""},
            "copilot": {"state": "WAITING", "reason": "in progress"},
        }
    }
    strategy_room = _health("WAITING", "WAITING", "WAITING", "WAITING", "WAITING", "WAITING", "WAITING")

    text = compact.warning_message(
        {"engineering": engineering, "strategy": strategy, "strategy_room": strategy_room}
    )

    for heading in (
        "<b>🤖 AI AGENT HEALTH</b>",
        "<b>🛠 ENGINEERING MONITOR</b>",
        "<b>🧠 STRATEGY MONITOR</b>",
        "<b>🧠 STRATEGY FACTORY</b>",
    ):
        assert heading in text

    # Provider/agent reachability is shown once in AI AGENT HEALTH. Operational
    # lanes collapse normal rows into counts and expand only actual issues.
    for label in ("GPT", "Claude", "Gemini", "DeepSeek", "Grok", "Kimi", "Copilot"):
        assert text.count(f" {label} —") >= 1

    assert "4 working · 1 in progress · 2 issues" in text
    assert "5 working · 1 in progress · 1 issues" in text
    assert "0 working · 7 in progress · 0 issues" in text
    assert "First status = agent/provider" not in text
    assert "Factory status is work state" not in text

    # The underlying classification contract remains available for dedicated
    # /aiaudit and /aistrategy drill-down views.
    assert compact.classify_health(
        "engineering", "claude", engineering["agents"]["claude"]
    ) == ("🟠", "Pipeline failure")
    assert compact.classify_health(
        "engineering", "deepseek", engineering["agents"]["deepseek"]
    ) == ("🔴", "Model config")
    assert compact.classify_health(
        "engineering", "copilot", engineering["agents"]["copilot"]
    ) == ("🟡", "In progress")


def test_master_dashboard_health_logic_is_still_available_for_drill_down():
    engineering = _health("WORKING", "WORKING", "WORKING", "FAILED", "WORKING", "WORKING", "WORKING")
    assert compact._overall_icon(engineering) == "🔴"


def test_master_dashboard_waiting_health_logic_is_still_available():
    strategy = _health("WORKING", "WORKING", "WAITING", "WORKING", "WORKING", "WORKING", "WORKING")
    assert compact._overall_icon(strategy) == "🟡"


def test_health_classification_uses_real_state_and_reason():
    _ensure_kimi_roster()
    assert compact.classify_health("engineering", "gemini", {"state": "WORKING"}) == ("🟢", "Working")
    assert compact.classify_health("strategy", "grok", {"state": "WORKING"}) == ("🟢", "Working")
    assert compact.classify_health("strategy", "kimi", {"state": "WORKING"}) == ("🟢", "Working")
    assert compact.classify_health("strategy", "copilot", {"state": "WAITING"}) == ("🟡", "In progress")
    assert compact.classify_health(
        "engineering", "copilot", {"state": "NOT_WORKING", "reason": "authentication failed"}
    ) == ("🔴", "Authentication")
    assert compact.classify_health(
        "engineering", "gemini", {"state": "NOT_WORKING", "reason": "network timeout"}
    ) == ("🔴", "Provider/network")
