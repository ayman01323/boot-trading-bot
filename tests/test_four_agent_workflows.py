from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_claude_is_available_in_central_factory_and_rotation_without_own_cron() -> None:
    assert not (ROOT / ".github/workflows/claude-fourth-strategy-agent.yml").exists()
    assert not (ROOT / ".github/workflows/claude-fourth-engineering-agent.yml").exists()
    central = (ROOT / "scripts/central_report_scheduler.py").read_text(encoding="utf-8")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in central
    assert 'NON_GPT_REVIEWERS = tuple(a for a in AGENTS if a != "gpt")' in central
    assert 'reviewer = NON_GPT_REVIEWERS[rotation_slot % len(NON_GPT_REVIEWERS)]' in central
    assert 'ops._ask("gpt", gpt_prompt' in central


def test_old_four_agent_master_is_retired_and_factory_uses_seven_agents() -> None:
    assert not (ROOT / ".github/workflows/four-agent-strategy-master.yml").exists()
    assert not (ROOT / ".github/workflows/selected-ai-master.yml").exists()
    central = (ROOT / "scripts/central_report_scheduler.py").read_text(encoding="utf-8")
    assert 'AGENTS = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in central
    assert "ops._panel_for = lambda package: list(AGENTS)" in central
    assert 'out["master"] = "gpt"' in central


def test_legacy_auto_code_gate_is_disabled_without_four_agent_evidence() -> None:
    text = (ROOT / "learnerbot/three_agent_strategy_contract.py").read_text(encoding="utf-8")
    assert '"claude"' in text
    assert 'decision.get("four_agent_evidence") is True' in text
    assert "len(agents) < 3" in text
    assert '"minimum_independent_agents": 3' in text
    assert '"auto_deploy": False' in text
