from learnerbot import telegram_ai_health_truth_patch as truth


def _health(states):
    return {
        "agents": {
            provider: {"state": state, "reason": reason}
            for provider, (state, reason) in states.items()
        }
    }


def test_lane_drilldown_contains_pipeline_state_only():
    health = _health(
        {
            "gpt": ("WORKING", "HEALTHY"),
            "claude": ("WAITING", "report refresh in progress"),
            "gemini": ("WORKING", "HEALTHY"),
            "deepseek": ("NOT_WORKING", "unsupported model config"),
            "grok": ("NOT_WORKING", "pipeline failed"),
            "kimi": ("WORKING", "HEALTHY"),
            "copilot": ("WAITING", "waiting for Copilot report"),
        }
    )

    text = truth.lane_text("engineering", health)

    assert "First status =" not in text
    assert "API working" not in text
    assert "API check stale" not in text
    assert "🟢 GPT — Working" in text
    assert "🟡 Claude — In progress" in text
    assert "🟢 Kimi — Working" in text
    assert "🔴 DeepSeek — Model config" in text
    assert "🟠 Grok — Pipeline failure" in text


def test_provider_health_is_shown_once_and_independent_of_review_failure(monkeypatch):
    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {
            "_truth_stale": False,
            "_truth_age_seconds": 30,
            "openai": {"state": "WORKING"},
            "anthropic": {"state": "WORKING"},
            "deepseek": {"state": "WORKING"},
            "xai": {"state": "WORKING"},
        },
    )
    monkeypatch.setattr(truth, "_copilot_assignment_state", lambda lane, health: "ASSIGNED")

    engineering = _health(
        {
            "gpt": ("WORKING", "HEALTHY"),
            "claude": ("WAITING", "refreshing"),
            "gemini": ("WORKING", "HEALTHY"),
            "deepseek": ("WAITING", "refreshing"),
            "grok": ("WAITING", "refreshing"),
            "kimi": ("WORKING", "HEALTHY"),
            "copilot": ("WAITING", "waiting"),
        }
    )
    strategy = _health(
        {
            "gpt": ("WORKING", "HEALTHY"),
            "claude": ("WORKING", "HEALTHY"),
            "gemini": ("WORKING", "HEALTHY"),
            "deepseek": ("NOT_WORKING", "unsupported model config"),
            "grok": ("NOT_WORKING", "pipeline failed"),
            "kimi": ("WORKING", "HEALTHY"),
            "copilot": ("WAITING", "waiting"),
        }
    )

    providers = truth.provider_health_text(engineering, strategy)
    strategy_summary = truth.lane_summary_text("strategy", strategy)

    assert "🟢 GPT — API working" in providers
    assert "🟢 Claude — API working" in providers
    assert "🟢 Grok — API working" in providers
    assert "🔴 Grok — API/provider problem" not in providers
    assert "Kimi" in providers
    assert "🔴 DeepSeek — Model config" in strategy_summary
    assert "🟠 Grok — Pipeline failure" in strategy_summary


def test_stale_api_evidence_shows_age_and_escalates_when_too_old(monkeypatch):
    engineering = _health({"gemini": ("WORKING", "HEALTHY"), "kimi": ("WORKING", "HEALTHY"), "copilot": ("WAITING", "waiting")})
    strategy = _health({"gemini": ("WORKING", "HEALTHY"), "kimi": ("WORKING", "HEALTHY"), "copilot": ("WAITING", "waiting")})
    monkeypatch.setattr(truth, "_copilot_assignment_state", lambda lane, health: "ASSIGNED")

    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {
            "_truth_stale": True,
            "_truth_age_seconds": 1500,
            "openai": {"state": "WORKING"},
            "anthropic": {"state": "WORKING"},
            "deepseek": {"state": "WORKING"},
            "xai": {"state": "WORKING"},
        },
    )
    text = truth.provider_health_text(engineering, strategy)
    assert "🟡 GPT — API not checked for 25m · last OK" in text

    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {
            "_truth_stale": True,
            "_truth_age_seconds": 4 * 60 * 60,
            "openai": {"state": "WORKING"},
            "anthropic": {"state": "WORKING"},
            "deepseek": {"state": "WORKING"},
            "xai": {"state": "WORKING"},
        },
    )
    text = truth.provider_health_text(engineering, strategy)
    assert "🔴 GPT — API unverified for 4h · last OK" in text


def test_master_operational_sections_are_compact_and_only_expand_issues(monkeypatch):
    monkeypatch.setattr(
        truth,
        "_fresh_preflight",
        lambda: {
            "_truth_stale": False,
            "_truth_age_seconds": 10,
            "openai": {"state": "WORKING"},
            "anthropic": {"state": "WORKING"},
            "deepseek": {"state": "WORKING"},
            "xai": {"state": "WORKING"},
        },
    )
    monkeypatch.setattr(truth, "_copilot_assignment_state", lambda lane, health: "ASSIGNED")

    engineering = _health(
        {
            provider: ("WAITING", "review in progress")
            for provider in truth._compact.PROVIDERS
        }
    )
    strategy = _health(
        {
            "gpt": ("WORKING", "HEALTHY"),
            "claude": ("WORKING", "HEALTHY"),
            "gemini": ("WORKING", "HEALTHY"),
            "deepseek": ("NOT_WORKING", "unsupported model config"),
            "grok": ("NOT_WORKING", "pipeline failed"),
            "kimi": ("WORKING", "HEALTHY"),
            "copilot": ("WAITING", "review in progress"),
        }
    )
    factory = _health(
        {
            provider: ("WAITING", "factory in progress")
            for provider in truth._compact.PROVIDERS
        }
    )

    engineering_summary = truth.lane_summary_text("engineering", engineering)
    strategy_summary = truth.lane_summary_text("strategy", strategy)
    factory_summary = truth.factory_summary_text(factory)
    dashboard = truth.dashboard_text(engineering, strategy, factory)

    assert "🟡 <b>0 working · 7 in progress · 0 issues</b>" in engineering_summary
    assert engineering_summary.count(" — ") == 0

    assert "🔴 <b>4 working · 1 in progress · 2 issues</b>" in strategy_summary
    assert "🔴 DeepSeek — Model config" in strategy_summary
    assert "🟠 Grok — Pipeline failure" in strategy_summary
    assert strategy_summary.count(" — ") == 2

    assert "🟡 <b>0 working · 7 in progress · 0 issues</b>" in factory_summary
    assert factory_summary.count(" — ") == 0

    assert "First status = agent/provider" not in dashboard
    assert "Factory status is work state" not in dashboard
