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
    # Claude is allowed to create only its temporary report workspace; any tracked
    # or out-of-scope edit causes the independent review step to fail.
    assert "['git','status','--porcelain']" in text
    assert "Claude reviewer changed tracked/out-of-scope files" in text


def test_claude_strategy_review_is_independent_of_copilot_and_deduplicated() -> None:
    text = (ROOT / ".github/workflows/claude-fourth-strategy-agent.yml").read_text(encoding="utf-8")
    # A failed/blocked original three-agent cycle must not suppress Claude.
    assert "github.event.workflow_run.conclusion == 'success'" not in text
    assert "types: [completed]" in text
    # Five-minute fallback makes the latest cycle recover quickly if the workflow_run
    # event is delayed, but an existing exact-cycle report prevents another paid call.
    assert "cron: '*/5 * * * *'" in text
    assert "Skip duplicate Claude API review for an already-published exact cycle" in text
    assert "no Anthropic API call will be made" in text
    assert "steps.existing.outputs.found != 'true' && steps.meta.outputs.evidence_match == 'true'" in text


def test_claude_paid_smoke_test_is_manual_only() -> None:
    text = (ROOT / ".github/workflows/claude-agent-smoke.yml").read_text(encoding="utf-8")
    assert "pull_request:" not in text
    assert "workflow_dispatch:" in text
    assert "run_paid_check" in text
    assert "if: inputs.run_paid_check == true" in text


def test_legacy_four_agent_master_delegates_to_selected_resilient_master() -> None:
    legacy = (ROOT / ".github/workflows/four-agent-strategy-master.yml").read_text(encoding="utf-8")
    selected = (ROOT / ".github/workflows/selected-ai-master.yml").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/resilient_selected_master.py").read_text(encoding="utf-8")
    fallback = (ROOT / "scripts/resilient_selected_master_v2.py").read_text(encoding="utf-8")

    assert "selected-ai-master.yml" in legacy
    assert "lane=strategy" in legacy
    assert "Four-agent completion is no longer required" in legacy
    assert "matrix:" in selected and "strategy, engineering" in selected
    assert 'if [[ "$count" == 0 ]]' in selected
    assert '"minimum_valid_reports_to_continue": 1' in runner
    assert '_FALLBACK = ("gpt", "claude", "gemini", "copilot")' in fallback
    assert '"live_auto_deploy": False' in runner


def test_legacy_auto_code_gate_is_disabled_without_four_agent_evidence() -> None:
    text = (ROOT / "learnerbot/three_agent_strategy_contract.py").read_text(encoding="utf-8")
    assert '"claude"' in text
    assert 'decision.get("four_agent_evidence") is True' in text
    assert "len(agents) < 3" in text
    assert '"minimum_independent_agents": 3' in text
    assert '"auto_deploy": False' in text
