from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_telegram_strategy_review_is_seven_agent_and_includes_kimi() -> None:
    patch = (ROOT / "learnerbot" / "telegram_kimi_seventh_review_patch.py").read_text(encoding="utf-8")
    scope = (ROOT / "learnerbot" / "telegram_command_scope_patch.py").read_text(encoding="utf-8")
    assert 'PROVIDERS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in patch
    assert '"kimi": "Kimi"' in patch
    assert "All seven agents analyse the same immutable strategy cycle and evidence set." in patch
    assert "Progress: {completed} of 7 completed" in patch
    assert "SEVEN-AGENT STRATEGY REVIEW" in patch
    assert "SEVEN STRATEGY AGENTS COMPLETE" in patch
    assert 'f"strategy/runs/{cycle}/kimi.json"' in patch
    assert "telegram_kimi_seventh_review_patch" in scope
    assert scope.index("telegram_grok_council_patch") < scope.index("telegram_kimi_seventh_review_patch")


def test_kimi_seventh_strategy_workflow_is_immutable_and_report_only() -> None:
    body = (ROOT / ".github" / "workflows" / "kimi-seventh-strategy-agent.yml").read_text(encoding="utf-8")
    assert "name: Kimi Seventh Strategy Agent" in body
    assert 'workflows: ["Hourly Three-Agent Strategy Cycle"]' in body
    assert "SOURCE_SHA" in body
    assert "EVIDENCE_SHA" in body
    assert "evidence_exact" in body
    assert "git checkout --detach \"$SOURCE_SHA\"" in body
    assert '"provider":"kimi"' in body
    assert '"scope":"MULTI_AGENT_STRATEGY_REVIEW"' in body
    assert '"review_only":true' in body
    assert '"no_live_changes":true' in body
    assert "strategy/runs/${CYCLE_ID}/kimi.json" in body
    assert "KIMI_API_KEY" in body
    assert "MOONSHOT_API_KEY" in body
    assert "do not edit code, trade, deploy" in body.lower()


def test_kimi_remains_advisory_not_a_protected_live_gate() -> None:
    existing = (ROOT / "tests" / "test_kimi_seventh_agent.py").read_text(encoding="utf-8")
    assert 'assert "kimi" not in cost.ALL_ADVISERS' in existing
