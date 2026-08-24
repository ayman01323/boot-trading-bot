from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_fast_deterministic_monitors_remain_fast_while_paid_ai_is_slow():
    monitor = _text(".github/workflows/monitor-factory-operations.yml")
    paid = _text(".github/workflows/hourly-three-agent-strategy-cycle.yml")
    provider = _text(".github/workflows/ai-provider-preflight.yml")
    assert "cron: '*/15 * * * *'" in monitor
    assert "cron: '7 * * * *'" in monitor
    assert "cron: '17 * * * *'" in monitor
    assert "cron: '17 2 * * *'" in paid
    assert "cron: '11 */4 * * *'" in provider
    assert "paid_inference_requested':False" in provider or "'paid_inference_requested':False" in provider or "'paid_inference_requested': False" in provider


def test_provider_preflight_covers_complete_seven_agent_family_without_inference():
    text = _text(".github/workflows/ai-provider-preflight.yml")
    for provider in ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot"):
        assert f"'{provider}':" in text
    assert "SEVEN_AGENT_AUTH_METADATA_CHECK_NO_MODEL_INFERENCE" in text
    assert "paid_inference_requested" in text


def test_kimi_is_seventh_engineering_reviewer_without_its_own_polling_schedule():
    text = _text(".github/workflows/kimi-seventh-engineering-agent.yml")
    assert "name: Kimi Seventh Engineering Agent" in text
    assert 'workflows: ["Grok Sixth Engineering Agent"]' in text
    assert "schedule:" not in text
    assert "workflow_dispatch:" in text
    assert "call_kimi" in text
    assert "REPORT ONLY" in text
    assert "no_live_changes" in text
    assert "weekly/runs/${SOURCE_SHA}/kimi.json" in text


def test_selected_master_and_runtime_health_share_same_seven_agents():
    wrapper = _text("scripts/resilient_selected_master_v2.py")
    health = _text("learnerbot/ai_four_agent_health_patch.py")
    fallback = '("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")'
    assert fallback in wrapper
    assert fallback in health


def test_legacy_high_frequency_master_pollers_are_disabled():
    for path in (
        ".github/workflows/weekly-resilient-master.yml",
        ".github/workflows/strategy-resilient-master.yml",
        ".github/workflows/gpt-master-cycle-dispatcher.yml",
    ):
        text = _text(path)
        assert "workflow_dispatch:" in text
        assert "schedule:" not in text
        assert "cron:" not in text
