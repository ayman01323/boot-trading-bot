from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_engineering_copilot_reconciler_uses_user_scoped_assignment_token():
    text = _text(".github/workflows/engineering-copilot-assignment-reconciler.yml")
    assert "COPILOT_ASSIGN_TOKEN: ${{ secrets.COPILOT_ASSIGN_TOKEN }}" in text
    assert "copilot-swe-agent[bot]" in text
    assert "agent_assignment" in text
    assert "base_branch':'main'" in text
    assert "assignment_state" in text
    assert "for delay in 0 5 10 20" in text
    assert 'any(. == "copilot" or . == "copilot-swe-agent[bot]")' in text
    assert "state='AWAITING_ASSIGNMENT'" in text
    assert "API accepted the request" in text


def test_engineering_agents_retry_every_thirty_minutes_on_same_source():
    text = _text(".github/workflows/engineering-agent-retry.yml")
    assert "cron: '17,47 * * * *'" in text
    assert "ref: ${{ steps.meta.outputs.source }}" in text
    assert "weekly/latest_source_commit.txt?ref=ai-reviews" in text
    assert "Retry GPT engineering report" in text
    assert "Retry Gemini engineering report" in text
    assert "Publish only successful retry reports" in text


def test_legacy_engineering_master_delegates_to_selected_resilient_master():
    compat = _text(".github/workflows/weekly-resilient-master.yml")
    selected = _text(".github/workflows/selected-ai-master.yml")
    runner = _text("scripts/resilient_selected_master.py")
    assert "selected-ai-master.yml" in compat
    assert "lane=engineering" in compat
    assert "matrix:" in selected and "strategy, engineering" in selected
    assert 'if [[ "$count" == 0 ]]' in selected
    assert '"minimum_valid_reports_to_continue": 1' in runner
    assert '"live_trading_depends_on_ai_health": False' in runner


def test_strategy_promoter_remains_shadow_first_and_never_toggles_live():
    text = _text(".github/workflows/strategy-approved-change-promoter.yml")
    assert "row.get('policy_eligible')" in text
    assert "row.get('shadow_only') is True" in text
    assert "strategy_auto_path_allowed" in text
    assert "Run full regression and critical trading-safety tests" in text
    assert "live_switch_changed':False" in text
    assert "CANARY->PROBATION->ACTIVE" in text
    assert "--admin" not in text


def test_runtime_loads_six_agent_health_warning_and_exact_source_guard():
    control = _text("learnerbot/ai_master_control.py")
    health = _text("learnerbot/ai_four_agent_health_patch.py")
    hi = _text("learnerbot/telegram_hi_keefek_patch.py")
    assert "ai_four_agent_health_patch" in control
    assert 'PROVIDERS = ("gpt", "claude", "gemini", "deepseek", "grok", "copilot")' in health
    assert "AI failure never disables the trading engine" in health
    assert "ai_agent_health_warning_patch" in hi
    assert "strategy_canary_source_guard_patch" in hi
