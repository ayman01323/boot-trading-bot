from learnerbot import ai_health_compact_report_patch as compact


def _health(*states):
    providers = ("gpt", "claude", "gemini", "deepseek", "copilot")
    return {
        "agents": {
            provider: {"state": state, "reason": state}
            for provider, state in zip(providers, states)
        }
    }


def test_master_dashboard_keeps_original_rows_and_only_renames_sections():
    engineering = {
        "agents": {
            "gpt": {"state": "WORKING", "reason": ""},
            "claude": {"state": "NOT_WORKING", "reason": "pipeline failed"},
            "gemini": {"state": "WORKING", "reason": ""},
            "deepseek": {"state": "NOT_WORKING", "reason": "unsupported model config"},
            "copilot": {"state": "WAITING", "reason": "in progress"},
        }
    }
    strategy = {
        "agents": {
            "gpt": {"state": "WORKING", "reason": ""},
            "claude": {"state": "WORKING", "reason": ""},
            "gemini": {"state": "WORKING", "reason": ""},
            "deepseek": {"state": "NOT_WORKING", "reason": "unsupported model config"},
            "copilot": {"state": "WAITING", "reason": "in progress"},
        }
    }
    strategy_room = _health("WAITING", "WAITING", "WAITING", "WAITING", "WAITING")

    text = compact.warning_message(
        {"engineering": engineering, "strategy": strategy, "strategy_room": strategy_room}
    )

    assert text == "\n\n".join(
        [
            "<b>🤖 AI AGENT HEALTH</b>",
            "\n".join(
                [
                    "<b>🛠 ENGINEERING MONITOR</b>",
                    "🟢 GPT — Working",
                    "🟠 Claude — Pipeline failure",
                    "🟢 Gemini — Working",
                    "🔴 DeepSeek — Model config",
                    "🟡 Copilot — In progress",
                ]
            ),
            "\n".join(
                [
                    "<b>🧠 STRATEGY MONITOR</b>",
                    "🟢 GPT — Working",
                    "🟢 Claude — Working",
                    "🟢 Gemini — Working",
                    "🔴 DeepSeek — Model config",
                    "🟡 Copilot — In progress",
                ]
            ),
            "\n".join(
                [
                    "<b>🧠 STRATEGY FACTORY</b>",
                    "🟡 GPT — In progress",
                    "🟡 Claude — In progress",
                    "🟡 Gemini — In progress",
                    "🟡 DeepSeek — In progress",
                    "🟡 Copilot — In progress",
                ]
            ),
        ]
    )


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
