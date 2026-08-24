from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_strategy_copilot_reconciler_retries_with_standard_assignment_payload():
    text = _text(".github/workflows/strategy-copilot-assignment-reconciler.yml")
    assert "cron: '*/10 * * * *'" in text
    assert "COPILOT_ASSIGN_TOKEN: ${{ secrets.COPILOT_ASSIGN_TOKEN }}" in text
    assert "copilot-swe-agent[bot]" in text
    assert "payload='{\"assignees\":[\"copilot-swe-agent[bot]\"]}'" in text
    assert "for delay in 0 5 10 20" in text
    assert "copilot_assignment_reconciled.json" in text
    assert "Repair latest strategy Copilot assignment status" in text


def test_strategy_master_continues_with_one_or_more_valid_reports_without_legacy_polling():
    compat = _text(".github/workflows/strategy-resilient-master.yml")
    selected = _text(".github/workflows/selected-ai-master.yml")
    runner = _text("scripts/resilient_selected_master.py")
    assert "selected-ai-master.yml" in compat
    assert "lane=strategy" in compat
    assert "schedule:" not in compat
    assert 'if [[ "$count" == 0 ]]' in selected
    assert '"minimum_valid_reports_to_continue": 1' in runner
    assert 'resilient_cycle_continued' in runner
    assert 'failed_agent_count' in runner
    assert '"live_auto_deploy": False' in runner


def test_single_agent_strategy_cycle_is_stricter_but_not_stopped():
    runner = _text("scripts/resilient_selected_master.py")
    assert 'threshold = 0.95 if count == 1 else 0.85' in runner
    assert 'if risk not in ({"LOW"} if count == 1 else {"LOW", "MEDIUM"})' in runner
    assert '"single_agent_strategy_confidence": 0.95' in runner
    assert '"live_trading_depends_on_ai_health": False' in runner


def test_selected_master_fallback_order_is_selected_then_all_seven_agents():
    wrapper = _text("scripts/resilient_selected_master_v2.py")
    assert '_FALLBACK = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in wrapper
    assert '(*_base.PROVIDERS, "deepseek", "grok", "kimi")' in wrapper
    assert 'if provider == "kimi"' in wrapper
    assert 'if preferred in _base.PROVIDERS' in wrapper
    assert 'if provider not in out' in wrapper
    assert 'max(0, 7 - len(valid_reports))' in wrapper


def test_selected_master_is_event_driven_and_dedupes_before_expensive_cli_setup():
    selected = _text(".github/workflows/selected-ai-master.yml")
    assert "Kimi Seventh Strategy Agent" in selected
    assert "Kimi Seventh Engineering Agent" in selected
    assert "schedule:" not in selected
    assert "Skip already-adjudicated unchanged report set" in selected
    assert "Install selectable MASTER CLIs only when adjudication is needed" in selected
    assert selected.index("Skip already-adjudicated unchanged report set") < selected.index("Install selectable MASTER CLIs only when adjudication is needed")
    assert "gpt gemini claude deepseek grok kimi" in selected
    assert "KIMI_API_KEY" in selected
