from learnerbot import telegram_ai_health_mobile_layout_patch as mobile


def _health(state="WORKING", reason="ok"):
    return {
        "available": True,
        "agents": {
            provider: {"state": state, "reason": reason}
            for provider in mobile._compact.PROVIDERS
        },
    }


def test_mobile_provider_layout_uses_single_compact_rows(monkeypatch):
    monkeypatch.setattr(
        mobile._truth,
        "_fresh_preflight",
        lambda: {
            "openai": {"state": "WORKING"},
            "anthropic": {"state": "WORKING"},
            "deepseek": {"state": "WORKING"},
            "xai": {"state": "WORKING"},
            "_truth_stale": True,
            "_truth_age_seconds": 20 * 60,
        },
    )
    monkeypatch.setattr(
        mobile._truth,
        "_runtime_connections",
        lambda: {
            "available": True,
            "connected_agents": set(mobile._compact.PROVIDERS),
            "updated_epoch": 1,
        },
    )

    text = mobile.provider_health_text(_health(), _health())

    assert "6 healthy</b> · 0 verify · 0 issues" in text
    assert " GPT — Online · API OK 20m" in text
    assert " Claude — Online · API OK 20m" in text
    assert " Gemini — Online" in text
    assert "↳" not in text
    assert "<i>" not in text
    assert "Worker connected · API last OK" not in text


def test_dashboard_uses_one_line_monitor_statuses(monkeypatch):
    monkeypatch.setattr(mobile._truth, "_review_stale_reason", lambda lane, health: "old")
    monkeypatch.setattr(
        mobile,
        "provider_health_text",
        lambda engineering, strategy: "<b>🤖 AI AGENT HEALTH</b>\n🟢 <b>6 healthy</b> · 0 verify · 0 issues",
    )
    room = _health(state="WAITING", reason="no strategy room request")

    text = mobile.dashboard_text(_health(), _health(), room)

    assert "<b>🛠 ENGINEERING MONITOR</b>\n🟡 <b>Snapshot stale</b> · refresh needed" in text
    assert "<b>🧠 STRATEGY MONITOR</b>\n🟡 <b>Snapshot stale</b> · refresh needed" in text
    assert "<b>🧠 STRATEGY FACTORY</b>\n⚪ <b>Idle</b> · no active request" in text
    assert "predates current code" not in text
