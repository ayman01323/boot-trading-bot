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


def test_strategy_master_continues_with_one_or_more_valid_reports():
    text = _text(".github/workflows/strategy-resilient-master.yml")
    assert "steps.agents.outputs.count != '0'" in text
    assert "One or two agents may be missing" in text
    assert "Never invent their opinions" in text
    assert "Deterministic fallback when GPT Master is unavailable" in text
    assert "three_agent_reports_complete':len(valid)==3" in text
    assert "'cycle_continued':True" in text
    assert "MASTER_DECIDED_PARTIAL" in text
    assert "live_auto_deploy':False" in text


def test_partial_strategy_master_cannot_bypass_two_agent_policy_gate():
    contract = _text("learnerbot/three_agent_strategy_contract.py")
    workflow = _text(".github/workflows/strategy-resilient-master.yml")
    assert 'if len(agents) < 2:' in contract
    assert 'requires support from at least two independent agents' in contract
    assert 'Any accepted auto-code proposal still requires at least two independent supporting agents' in workflow
