from pathlib import Path


def test_hourly_three_agent_strategy_cycle_is_independent_of_weekly_engineering():
    text = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hourly-three-agent-strategy-cycle.yml").read_text(encoding="utf-8")

    assert "cron: '17 * * * *'" in text
    assert "workflow_dispatch:" in text
    assert "Weekly GPT Master Corrective Action" not in text
    assert "OPENAI_API_KEY" in text
    assert "GEMINI_API_KEY" in text
    assert "copilot-swe-agent[bot]" in text
    assert "THREE_AGENT_STRATEGY_REVIEW" in text
    assert "strategy/latest_status.json" in text
    assert "live_auto_deploy':False" in text
