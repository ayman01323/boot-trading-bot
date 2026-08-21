from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_WF = ROOT / ".github/workflows/claude-fourth-strategy-agent.yml"
HOURLY_WF = ROOT / ".github/workflows/hourly-three-agent-strategy-cycle.yml"


def _text() -> str:
    return CLAUDE_WF.read_text(encoding="utf-8")


def test_no_longer_chained_via_workflow_run():
    text = _text()
    assert "workflow_run:" not in text
    assert 'workflows: ["Hourly Three-Agent Strategy Cycle"]' not in text


def test_runs_on_the_same_schedule_as_the_hourly_cycle():
    claude_text = _text()
    hourly_text = HOURLY_WF.read_text(encoding="utf-8")
    assert "cron: '17 */4 * * *'" in claude_text
    assert "cron: '17 */4 * * *'" in hourly_text


def test_resolves_its_own_source_and_cycle_id_independently():
    text = _text()
    assert 'SOURCE_SHA="$(git rev-parse HEAD)"' in text
    assert "HOUR_KEY=" in text
    assert 'CYCLE_ID="${INPUT_CYCLE:-${SOURCE_SHA:0:12}-${HOUR_KEY}-${EVIDENCE_SHA:0:8}}"' in text
    # Must not depend on the other workflow having already published these.
    assert "strategy/latest_cycle_id.txt?ref=ai-reviews" not in text
    assert "strategy/runs/${cycle}/context.json?ref=ai-reviews" not in text


def test_uses_its_own_cost_gate_state_key_not_the_shared_one():
    text = _text()
    assert "strategy/claude_cost_gate_state.json" in text
    # Must never read or write the hourly workflow's own state file -- sharing it
    # risks both workflows racing to push the same path around the same time.
    assert "strategy/cost_gate_state.json?ref=ai-reviews" not in text
    assert 'strategy/cost_gate_state.json"' not in text


def test_paid_claude_steps_are_gated_on_material_change():
    text = _text()
    assert "if: steps.gate.outputs.run_ai == 'true'" in text


def test_publish_step_tolerates_missing_context_and_status_files():
    text = _text()
    marker = "- name: Publish Claude into the shared strategy cycle"
    start = text.find(marker)
    assert start >= 0
    block = text[start:]
    assert "try:" in block and "except Exception:" in block
    assert "existing_cycle" in block


def test_clean_workspace_always_runs():
    assert "if: always()" in _text().split("- name: Clean workspace", 1)[1]
