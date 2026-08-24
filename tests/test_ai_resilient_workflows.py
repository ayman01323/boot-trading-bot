from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_legacy_fast_retry_and_master_workflows_are_retired():
    for path in (
        ".github/workflows/engineering-agent-retry.yml",
        ".github/workflows/engineering-copilot-assignment-reconciler.yml",
        ".github/workflows/strategy-copilot-assignment-reconciler.yml",
        ".github/workflows/selected-ai-master.yml",
        ".github/workflows/weekly-resilient-master.yml",
        ".github/workflows/strategy-resilient-master.yml",
        ".github/workflows/gpt-master-strategy-action.yml",
    ):
        assert not (ROOT / path).exists(), path


def test_central_factory_has_all_seven_agents_and_gpt_master():
    text = _text("scripts/central_report_scheduler.py")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in text
    assert "ops._panel_for = lambda package: list(AGENTS)" in text
    assert 'out["master"] = "gpt"' in text
    assert '"STRATEGY_FACTORY_REVIEW"' in text


def test_strategy_promoter_remains_shadow_first_and_never_toggles_live():
    text = _text(".github/workflows/strategy-approved-change-promoter.yml")
    assert "row.get('policy_eligible')" in text
    assert "row.get('shadow_only') is True" in text
    assert "strategy_auto_path_allowed" in text
    assert "Run full regression and critical trading-safety tests" in text
    assert "live_switch_changed':False" in text
    assert "CANARY->PROBATION->ACTIVE" in text
    assert "--admin" not in text


def test_runtime_loads_agent_health_warning_and_exact_source_guard():
    control = _text("learnerbot/ai_master_control.py")
    health = _text("learnerbot/ai_four_agent_health_patch.py")
    hi = _text("learnerbot/telegram_hi_keefek_patch.py")
    assert "ai_four_agent_health_patch" in control
    assert "AI failure never disables the trading engine" in health
    assert "ai_agent_health_warning_patch" in hi
    assert "strategy_canary_source_guard_patch" in hi
