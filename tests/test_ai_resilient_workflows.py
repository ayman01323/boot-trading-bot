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


def test_resilient_master_continues_with_at_least_one_valid_agent():
    text = _text(".github/workflows/weekly-resilient-master.yml")
    assert "steps.agents.outputs.count != '0'" in text
    assert "Deterministic fallback master when GPT Master is unavailable" in text
    assert "single-agent resilient cycle cannot auto-implement" in text
    assert "cycle_continued':True" in text
    assert "three_agent_reports_complete':n==3" in text


def test_strategy_promoter_is_policy_eligible_shadow_first_and_never_toggles_live():
    text = _text(".github/workflows/strategy-approved-change-promoter.yml")
    assert "row.get('policy_eligible')" in text
    assert "row.get('shadow_only') is True" in text
    assert "len(agents & {'gpt','gemini','copilot'})>=2" in text
    assert "strategy_auto_path_allowed" in text
    assert "Run full regression and critical trading-safety tests" in text
    assert "gh pr merge" in text
    assert "gh workflow run deploy-vps.yml --ref main" in text
    assert "live_switch_changed':False" in text
    assert "CANARY->PROBATION->ACTIVE" in text
    assert "--admin" not in text


def test_runtime_loads_health_warning_and_exact_source_guard():
    text = _text("learnerbot/telegram_hi_keefek_patch.py")
    assert "ai_agent_health_warning_patch" in text
    assert "strategy_canary_source_guard_patch" in text
