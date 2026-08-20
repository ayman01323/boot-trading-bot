from pathlib import Path


WORKFLOW = Path('.github/workflows/gpt-master-cycle-dispatcher.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_dispatcher_finds_copilot_pr_by_cycle_not_fixed_title_prefix():
    text = _text()
    assert '${cycle} in:title' in text
    assert 'Copilot hourly strategy review ${cycle} in:title' not in text
    assert 'gpt-master-strategy-action.yml' in text
    assert 'copilot_pr_number="$pr"' in text


def test_dispatcher_is_default_branch_scheduled_and_report_pipeline_only():
    text = _text()
    assert "cron: '3-59/5 * * * *'" in text
    assert 'workflow_dispatch:' in text
    assert 'actions: write' in text
    assert 'contents: read' in text
    assert 'pull-requests: read' in text
    assert 'master_decision.json?ref=ai-reviews' in text
    assert '--ref main' in text
