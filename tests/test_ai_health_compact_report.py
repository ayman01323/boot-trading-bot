from learnerbot import ai_health_compact_report_patch as compact


def _health(*states):
    providers = ("gpt", "claude", "gemini", "deepseek", "grok", "copilot")
    return {
        "agents": {
            provider: {"state": state, "reason": state}
            for provider, state in zip(providers, states)
        }
    }


def test_master_dashboard_keeps_all_sections_and_six_agent_rows_under_truth_overlay():
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

    # Presentation overlays may add provider/API truth beside review-pipeline
    # truth. Preserve the stable dashboard contract instead of pinning the old
    # pre-overlay sentence-by-sentence rendering.
    for heading in (
        "<b>🤖 AI AGENT HEALTH</b>",
        "<b>🛠 ENGINEERING MONITOR</b>",
        "<b>🧠 STRATEGY MONITOR</b>",
        "<b>🧠 STRATEGY FACTORY</b>",
    ):
        assert heading in text

    for label in ("GPT", "Claude", "Gemini", "DeepSeek", "Grok", "Copilot"):
        # Each provider must remain visible in engineering, strategy and factory.
        assert text.count(f" {label} —") >= 3

    # The underlying classification contract is still present even when an
    # outer truth overlay enriches the final line wording.
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
