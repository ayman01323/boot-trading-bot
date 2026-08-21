from pathlib import Path
import re

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hourly-three-agent-strategy-cycle.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _step_block(name: str) -> str:
    text = _text()
    marker = f"      - name: {name}"
    start = text.find(marker)
    assert start >= 0, f"missing workflow step: {name}"
    next_start = text.find("\n      - name: ", start + len(marker))
    return text[start:] if next_start < 0 else text[start:next_start]


def test_workflow_has_expected_step_count():
    names = re.findall(r"(?m)^\s{6}- name: .+$", _text())
    assert len(names) == 16


def test_cost_gate_step_exists_and_reads_evidence():
    gate = _step_block("Evaluate paid-AI cost gate")
    assert "id: gate" in gate
    assert "evaluate_cost_gate" in gate
    assert "strategy/cost_gate_state.json" in gate
    assert "evidence.json" in gate


def test_paid_steps_are_gated_on_material_change():
    for name in (
        "Prepare common strategy prompt",
        "GPT independent strategy report",
        "Gemini independent strategy report",
        "Create and attempt to assign Copilot the same strategy cycle",
        "Publish independent reports and reconciliable state",
    ):
        block = _step_block(name)
        assert "if: steps.gate.outputs.run_ai == 'true'" in block


def test_recovery_steps_stay_scoped_to_an_actual_attempt():
    assert "if: steps.gate.outputs.run_ai == 'true' && steps.gpt.outcome != 'success'" in _step_block("Recover GPT incomplete report")
    assert "if: steps.gate.outputs.run_ai == 'true' && steps.gemini.outcome != 'success'" in _step_block("Recover Gemini incomplete report")


def test_skip_note_only_fires_when_gate_says_no():
    skip = _step_block("Skip paid AI review -- no material change")
    assert "if: steps.gate.outputs.run_ai != 'true'" in skip


def test_install_and_credential_steps_remain_unconditional():
    # These don't call a paid AI API themselves. Assert there is no step-level if
    # before the first executable field instead of importing a YAML parser that is
    # not part of the production/test dependency set.
    for name in ("Install review tools", "Provider credential preflight"):
        block = _step_block(name)
        prefix = block.split("run:", 1)[0]
        assert "\n        if:" not in prefix


def test_clean_workspace_always_runs():
    assert "if: always()" in _step_block("Clean workspace")


def test_publish_step_persists_cost_gate_state_for_next_cycle():
    publish = _step_block("Publish independent reports and reconciliable state")
    assert "MATERIAL_SHA" in publish
    assert "last_ai_attempt_epoch" in publish
    assert "last_ai_attempt_source_commit" in publish
    assert "last_ai_attempt_material_sha256" in publish
    assert "cost_gate_state.json" in publish
