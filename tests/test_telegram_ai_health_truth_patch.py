from learnerbot import telegram_ai_health_truth_patch as truth


def test_lane_text_separates_provider_health_from_review_state(monkeypatch):
    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {
            "_truth_stale": False,
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
    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {"_truth_stale": False, "xai": {"state": "FAILED"}},
    )
    health = {
        "agents": {
            "grok": {"state": "WORKING", "reason": "HEALTHY"},
        }
    }

    text = truth.lane_text("strategy", health)

    assert "🔴 Grok — API/provider problem • 🟢 Strategy review working" in text


def test_stale_provider_check_never_copies_review_failure_into_provider_status(monkeypatch):
    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {
            "_truth_stale": True,
            "_truth_age_seconds": 1500,
            "anthropic": {"state": "WORKING"},
            "deepseek": {"state": "WORKING"},
            "xai": {"state": "WORKING"},
        },
    )
    health = {
        "agents": {
            "claude": {"state": "NOT_WORKING", "reason": "pipeline failed"},
            "deepseek": {"state": "NOT_WORKING", "reason": "unsupported model config"},
            "grok": {"state": "NOT_WORKING", "reason": "pipeline failed"},
        }
    }

    text = truth.lane_text("engineering", health)

    assert "🟡 Claude — API check stale (last working) • 🟠 Engineering review pipeline failure" in text
    assert "🟡 DeepSeek — API check stale (last working) • 🔴 Engineering review model config" in text
    assert "🟡 Grok — API check stale (last working) • 🟠 Engineering review pipeline failure" in text
    assert "Claude — Agent pipeline failure" not in text
    assert "DeepSeek — Agent model config" not in text
    assert "Grok — Agent pipeline failure" not in text
