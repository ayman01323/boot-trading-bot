from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_claude_workflow_uses_secret_plan_mode_same_cycle_and_clean_workspace() -> None:
    text = (ROOT / ".github/workflows/claude-fourth-strategy-agent.yml").read_text(encoding="utf-8")
    assert "secrets.ANTHROPIC_API_KEY" in text
    assert "@anthropic-ai/claude-code@latest" in text
    assert "--permission-mode plan" in text
    assert '"provider":"claude"' in text
    assert '"scope":"MULTI_AGENT_STRATEGY_REVIEW"' in text
    assert "evidence_sha256" in text
    assert "no_live_changes" in text
    assert "['git','status','--porcelain']" in text
    assert "Claude reviewer changed tracked/out-of-scope files" in text


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
