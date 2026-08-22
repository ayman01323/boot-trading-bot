from learnerbot import ai_health_compact_report_patch as compact


def _health(*states):
    providers = ("gpt", "claude", "gemini", "deepseek", "copilot")
    return {
        "agents": {
            provider: {"state": state, "reason": state}
            for provider, state in zip(providers, states)
        }
    }


def test_master_dashboard_matches_requested_compact_tree():
    engineering = _health("WORKING", "WORKING", "WORKING", "WORKING", "WORKING")
    strategy = _health("WORKING", "WORKING", "WORKING", "WORKING", "WORKING")
    strategy_room = _health("WORKING", "WORKING", "WORKING", "WORKING", "WAITING")

    text = compact.warning_message(
        {"engineering": engineering, "strategy": strategy, "strategy_room": strategy_room}
    )

    assert text == "\n".join(
        [
            "<b>🤖 AI AGENT HEALTH</b>",
            "│",
            "├─ <b>🛠 ENGINEERING MONITOR</b>",
            "├─ <b>🧠 STRATEGY MONITOR</b>",
            "└─ <b>🧠 STRATEGY FACTORY</b>",
        ]
    )
    assert "GPT —" not in text
    assert "Claude —" not in text
    assert "Gemini —" not in text
    assert "DeepSeek —" not in text
    assert "Copilot —" not in text
    assert "🟢" not in text
    assert "🟡" not in text
    assert "🔴" not in text


def test_master_dashboard_health_logic_is_still_available_for_drill_down():
    engineering = _health("WORKING", "WORKING", "WORKING", "FAILED", "WORKING")
    assert compact._overall_icon(engineering) == "🔴"


def test_master_dashboard_waiting_health_logic_is_still_available():
    strategy = _health("WORKING", "WORKING", "WAITING", "WORKING", "WORKING")
    assert compact._overall_icon(strategy) == "🟡"


def test_health_classification_uses_real_state_and_reason():
    assert compact.classify_health("engineering", "gemini", {"state": "WORKING"}) == ("🟢", "Working")
    assert compact.classify_health("strategy", "copilot", {"state": "WAITING"}) == ("🟡", "In progress")
    assert compact.classify_health(
        "engineering", "copilot", {"state": "NOT_WORKING", "reason": "authentication failed"}
    ) == ("🔴", "Authentication")
    assert compact.classify_health(
        "engineering", "gemini", {"state": "NOT_WORKING", "reason": "network timeout"}
    ) == ("🔴", "Provider/network")
