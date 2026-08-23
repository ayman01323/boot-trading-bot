from learnerbot import ai_health_compact_report_patch as compact


def _health(*states):
    providers = ("gpt", "claude", "gemini", "deepseek", "grok", "copilot")
    return {
        "agents": {
            provider: {"state": state, "reason": state}
            for provider, state in zip(providers, states)
        }
    }


def test_master_dashboard_separates_agent_health_from_review_pipeline():
    engineering = {
        "agents": {
            "gpt": {"state": "WORKING", "reason": ""},
            "claude": {"state": "NOT_WORKING", "reason": "pipeline failed"},
            "gemini": {"state": "WORKING", "reason": ""},
            "deepseek": {"state": "NOT_WORKING", "reason": "unsupported model config"},
            "grok": {"state": "WORKING", "reason": ""},
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
            "copilot": {"state": "WAITING", "reason": "in progress"},
        }
    }
    strategy_room = _health("WAITING", "WAITING", "WAITING", "WAITING", "WAITING", "WAITING")

    text = compact.warning_message(
        {"engineering": engineering, "strategy": strategy, "strategy_room": strategy_room}
    )

    assert "<b>🤖 AI AGENT HEALTH</b>" in text
    assert "<b>🛠 ENGINEERING MONITOR</b>" in text
    assert "<b>🧠 STRATEGY MONITOR</b>" in text
    assert "<b>🧠 STRATEGY FACTORY</b>" in text
    assert "First status = agent/provider • second status = review pipeline" in text
    assert "GPT — Agent working • 🟢 Engineering review working" in text
    assert "Claude — Agent pipeline failure • 🟠 Engineering review pipeline failure" in text
    assert "DeepSeek — Agent model config • 🔴 Engineering review model config" in text
    assert "Copilot — Agent state pending • 🟡 Engineering review in progress" in text
    assert "Claude — Agent working • 🟢 Strategy review working" in text
    assert "Factory status is work state, not provider/API health" in text
    assert "GPT — Factory in progress" in text


def test_master_dashboard_health_logic_is_still_available_for_drill_down():
    engineering = _health("WORKING", "WORKING", "WORKING", "FAILED", "WORKING", "WORKING")
    assert compact._overall_icon(engineering) == "🔴"


def test_master_dashboard_waiting_health_logic_is_still_available():
    strategy = _health("WORKING", "WORKING", "WAITING", "WORKING", "WORKING", "WORKING")
    assert compact._overall_icon(strategy) == "🟡"


def test_health_classification_uses_real_state_and_reason():
    assert compact.classify_health("engineering", "gemini", {"state": "WORKING"}) == ("🟢", "Working")
    assert compact.classify_health("strategy", "grok", {"state": "WORKING"}) == ("🟢", "Working")
    assert compact.classify_health("strategy", "copilot", {"state": "WAITING"}) == ("🟡", "In progress")
    assert compact.classify_health(
        "engineering", "copilot", {"state": "NOT_WORKING", "reason": "authentication failed"}
    ) == ("🔴", "Authentication")
    assert compact.classify_health(
        "engineering", "gemini", {"state": "NOT_WORKING", "reason": "network timeout"}
    ) == ("🔴", "Provider/network")
