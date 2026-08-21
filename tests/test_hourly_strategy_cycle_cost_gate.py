from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hourly-three-agent-strategy-cycle.yml"


def _doc():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps_by_name():
    return {s.get("name"): s for s in _doc()["jobs"]["review"]["steps"]}


def test_workflow_is_valid_yaml_with_expected_step_count():
    steps = _doc()["jobs"]["review"]["steps"]
    assert len(steps) == 16


def test_cost_gate_step_exists_and_reads_evidence():
    steps = _steps_by_name()
    gate = steps["Evaluate paid-AI cost gate"]
    assert gate["id"] == "gate"
    assert "evaluate_cost_gate" in gate["run"]
    assert "strategy/cost_gate_state.json" in gate["run"]
    assert "evidence.json" in gate["run"]


def test_paid_steps_are_gated_on_material_change():
    steps = _steps_by_name()
    for name in (
        "Prepare common strategy prompt",
        "GPT independent strategy report",
        "Gemini independent strategy report",
        "Create and attempt to assign Copilot the same strategy cycle",
        "Publish independent reports and reconciliable state",
    ):
        assert steps[name].get("if") == "steps.gate.outputs.run_ai == 'true'"


def test_recovery_steps_stay_scoped_to_an_actual_attempt():
    steps = _steps_by_name()
    assert steps["Recover GPT incomplete report"]["if"] == "steps.gate.outputs.run_ai == 'true' && steps.gpt.outcome != 'success'"
    assert steps["Recover Gemini incomplete report"]["if"] == "steps.gate.outputs.run_ai == 'true' && steps.gemini.outcome != 'success'"


def test_skip_note_only_fires_when_gate_says_no():
    steps = _steps_by_name()
    skip = steps["Skip paid AI review -- no material change"]
    assert skip["if"] == "steps.gate.outputs.run_ai != 'true'"


def test_install_and_credential_steps_remain_unconditional():
    # These don't call a paid AI API themselves (npm install, secret-presence
    # check only), so they aren't worth gating -- gating them would risk a
    # confusing partial environment on the next cycle that does need to run.
    steps = _steps_by_name()
    assert steps["Install review tools"].get("if") is None
    assert steps["Provider credential preflight"].get("if") is None


def test_clean_workspace_always_runs():
    steps = _steps_by_name()
    assert steps["Clean workspace"]["if"] == "always()"


def test_publish_step_persists_cost_gate_state_for_next_cycle():
    steps = _steps_by_name()
    publish = steps["Publish independent reports and reconciliable state"]
    assert "MATERIAL_SHA" in publish["env"]
    assert "last_ai_attempt_epoch" in publish["run"]
    assert "last_ai_attempt_source_commit" in publish["run"]
    assert "last_ai_attempt_material_sha256" in publish["run"]
    assert "cost_gate_state.json" in publish["run"]
