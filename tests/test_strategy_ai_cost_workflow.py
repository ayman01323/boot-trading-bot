from pathlib import Path


WORKFLOW = Path('.github/workflows/hourly-three-agent-strategy-cycle.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_hourly_schedule_is_retained_for_free_evidence_gate():
    text = _text()
    assert "cron: '17 * * * *'" in text
    assert 'id: cost_gate' in text
    assert 'learnerbot.strategy_ai_cost_gate' in text
    assert 'STRATEGY_AI_FORCE_REFRESH_SECONDS' in text
    assert '21600' in text


def test_paid_review_job_runs_only_when_gate_allows_it():
    text = _text()
    assert 'needs: gate' in text
    assert "if: needs.gate.outputs.run_ai == 'true'" in text
    gate_pos = text.index('gate:')
    review_pos = text.index('review:')
    install_pos = text.index('Install review tools')
    assert gate_pos < review_pos < install_pos


def test_manual_dispatch_and_material_or_source_change_can_force_review():
    text = _text()
    assert 'workflow_dispatch:' in text
    assert 'MANUAL_REQUEST' in text
    assert 'SOURCE_COMMIT_CHANGED' in text
    assert 'MATERIAL_EVIDENCE_CHANGED' in text
    assert 'FORCED_REFRESH_DUE' in text


def test_gate_persists_last_paid_attempt_without_touching_live_trading():
    text = _text()
    assert 'strategy/cost_gate/latest.json' in text
    assert 'last_ai_attempt_epoch' in text
    assert 'last_ai_attempt_material_sha256' in text
    assert 'last_ai_attempt_source_commit' in text
    assert 'LIVE' not in text[text.index('Persist paid-AI attempt checkpoint'):text.index('review:')]


def test_reviewer_prompt_caps_proposal_count():
    text = _text()
    assert 'Return no more than 3 proposals' in text
