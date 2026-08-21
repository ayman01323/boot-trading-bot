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

    assert "ENGINEERING" in text
    assert "GPT       🟠 REPORT/VALIDATION FAILURE — provider probably reachable" in text
    assert "CLAUDE    🟠 PIPELINE-SPECIFIC FAILURE" in text
    assert "GEMINI    🟢 WORKING" in text
    assert "DEEPSEEK  🔴 CLAUDE-CODE CUSTOM MODEL CONFIGURATION" in text
    assert "COPILOT   🟡 IN PROGRESS" in text
    assert "STRATEGY" in text
    assert "DEEPSEEK  🔴 SAME CONFIGURATION BUG" in text


def test_health_classification_uses_real_state_and_reason():
    assert compact.classify_health("engineering", "gemini", {"state": "WORKING"}) == "🟢 WORKING"
    assert compact.classify_health("strategy", "copilot", {"state": "WAITING"}) == "🟡 IN PROGRESS"
    assert compact.classify_health(
        "engineering", "copilot", {"state": "NOT_WORKING", "reason": "authentication failed"}
    ) == "🔴 AUTHENTICATION FAILURE"
    assert compact.classify_health(
        "engineering", "gemini", {"state": "NOT_WORKING", "reason": "network timeout"}
    ) == "🔴 PROVIDER/NETWORK FAILURE"
