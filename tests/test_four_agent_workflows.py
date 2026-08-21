from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_claude_workflow_uses_secret_plan_mode_and_same_cycle_identity() -> None:
    text = (ROOT / ".github/workflows/claude-fourth-strategy-agent.yml").read_text(encoding="utf-8")
    assert "secrets.ANTHROPIC_API_KEY" in text
    assert "@anthropic-ai/claude-code@latest" in text
    assert "--permission-mode plan" in text
    assert '"provider":"claude"' in text
    assert '"scope":"MULTI_AGENT_STRATEGY_REVIEW"' in text
    assert "evidence_sha256" in text
    assert "no_live_changes" in text
    assert "git status --porcelain" in text


def test_four_agent_master_requires_complete_set_and_three_of_four_policy() -> None:
    text = (ROOT / ".github/workflows/four-agent-strategy-master.yml").read_text(encoding="utf-8")
    assert "for provider in gpt gemini copilot claude" in text
    assert '"four_agent_evidence":true' in text
    assert "at least 3 of the 4" in text
    assert "live_auto_deploy" in text
    assert "MASTER_DECIDED_4_AGENT" in text
    assert "agents_adjudicated" in text


def test_legacy_auto_code_gate_is_disabled_without_four_agent_evidence() -> None:
    text = (ROOT / "learnerbot/three_agent_strategy_contract.py").read_text(encoding="utf-8")
    assert '"claude"' in text
    assert 'decision.get("four_agent_evidence") is True' in text
    assert "len(agents) < 3" in text
    assert '"minimum_independent_agents": 3' in text
    assert '"auto_deploy": False' in text
