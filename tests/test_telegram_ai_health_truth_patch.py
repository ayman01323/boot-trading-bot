from learnerbot import telegram_ai_health_truth_patch as truth


def test_lane_text_separates_provider_health_from_review_state(monkeypatch):
    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {
            "openai": {"state": "WORKING"},
            "anthropic": {"state": "WORKING"},
            "deepseek": {"state": "WORKING"},
            "xai": {"state": "WORKING"},
        },
    )
    monkeypatch.setattr(truth, "_copilot_assignment_state", lambda lane, health: "ASSIGNED")

    refreshing = {
        "state": "WAITING",
        "reason": "Provider API is healthy; replacement report is being refreshed",
        "provider_preflight": "WORKING",
    }
    health = {
        "source_commit": "a" * 40,
        "agents": {
            "gpt": {"state": "WORKING", "reason": "HEALTHY"},
            "claude": dict(refreshing),
            "gemini": {"state": "WORKING", "reason": "HEALTHY"},
            "deepseek": dict(refreshing),
            "grok": dict(refreshing),
            "copilot": {"state": "WAITING", "reason": "waiting for Copilot report"},
        },
    }

    text = truth.lane_text("engineering", health)

    assert "First status = agent/provider" in text
    assert "🟢 GPT — API working • 🟢 Engineering review working" in text
    assert "🟢 Claude — API working • 🟡 Engineering review refreshing" in text
    assert "🟢 Gemini — Agent working • 🟢 Engineering review working" in text
    assert "🟢 DeepSeek — API working • 🟡 Engineering review refreshing" in text
    assert "🟢 Grok — API working • 🟡 Engineering review refreshing" in text
    assert "🟢 Copilot — Assigned • 🟡 Engineering review in progress" in text


def test_provider_problem_is_not_hidden_by_review_status(monkeypatch):
    monkeypatch.setattr(truth, "_fresh_preflight", lambda: {"xai": {"state": "FAILED"}})
    health = {
        "agents": {
            "grok": {"state": "WORKING", "reason": "HEALTHY"},
        }
    }

    text = truth.lane_text("strategy", health)

    assert "🔴 Grok — API/provider problem • 🟢 Strategy review working" in text
