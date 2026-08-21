from learnerbot import ai_health_compact_report_patch as compact


def test_requested_compact_health_format():
    engineering = {
        "agents": {
            "gpt": {"state": "NOT_WORKING", "reason": "report schema validation failed"},
            "claude": {"state": "NOT_WORKING", "reason": "engineering pipeline report did not complete"},
            "gemini": {"state": "WORKING", "reason": "DONE"},
            "deepseek": {"state": "NOT_WORKING", "reason": "Claude-Code custom model configuration rejected"},
            "copilot": {"state": "WAITING", "reason": "assignment pending"},
        }
    }
    strategy = {
        "agents": {
            "gpt": {"state": "WORKING", "reason": "DONE"},
            "claude": {"state": "WORKING", "reason": "DONE"},
            "gemini": {"state": "WORKING", "reason": "DONE"},
            "deepseek": {"state": "NOT_WORKING", "reason": "Claude-Code custom model configuration rejected"},
            "copilot": {"state": "WAITING", "reason": "assignment pending"},
        }
    }

    text = compact.warning_message({"engineering": engineering, "strategy": strategy})

    assert text.startswith("🤖 AI AGENT HEALTH")
    assert "🛠 ENGINEERING" in text
    assert "🟠 GPT — Report validation" in text
    assert "🟠 Claude — Pipeline failure" in text
    assert "🟢 Gemini — Working" in text
    assert "🔴 DeepSeek — Model config" in text
    assert "🟡 Copilot — In progress" in text
    assert "🧠 STRATEGY" in text
    assert "🟢 GPT — Working" in text

    # Mobile presentation must not rely on padded columns or long diagnostics.
    assert "provider probably reachable" not in text
    assert "CLAUDE-CODE CUSTOM MODEL CONFIGURATION" not in text
    assert "       " not in text


def test_health_classification_uses_real_state_and_reason():
    assert compact.classify_health("engineering", "gemini", {"state": "WORKING"}) == ("🟢", "Working")
    assert compact.classify_health("strategy", "copilot", {"state": "WAITING"}) == ("🟡", "In progress")
    assert compact.classify_health(
        "engineering", "copilot", {"state": "NOT_WORKING", "reason": "authentication failed"}
    ) == ("🔴", "Authentication")
    assert compact.classify_health(
        "engineering", "gemini", {"state": "NOT_WORKING", "reason": "network timeout"}
    ) == ("🔴", "Provider/network")
