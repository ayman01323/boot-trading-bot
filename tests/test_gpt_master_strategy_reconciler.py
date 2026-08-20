from pathlib import Path


WORKFLOW = Path('.github/workflows/gpt-master-strategy-action.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_master_reconciler_runs_from_default_branch_schedule_not_bot_pr_event():
    text = _text()
    assert "cron: '*/10 * * * *'" in text
    assert 'workflow_dispatch:' in text
    assert '\n  pull_request:' not in text
    assert 'Copilot hourly strategy review ${cycle}' in text
    assert 'No completed Copilot report PR yet' in text


def test_master_has_no_bwrap_workspace_sandbox_or_persisted_credentials():
    text = _text()
    assert 'persist-credentials: false' in text
    assert 'codex --ask-for-approval never exec --sandbox danger-full-access --ephemeral' in text
    assert '--sandbox workspace-write' not in text
    assert 'CODEX_API_KEY: ${{ secrets.OPENAI_API_KEY }}' in text


def test_master_requires_all_three_complete_and_same_cycle():
    text = _text()
    assert 'Independent reports still incomplete' in text
    assert 'validate-agent' in text
    assert '[[ "$CYCLE_ID" == "${{ steps.pr.outputs.cycle_hint }}" ]]' in text
    assert 'three_agent_reports_complete' in text


def test_master_is_report_only_and_never_auto_deploys():
    text = _text()
    assert 'This run is REPORT ONLY' in text
    assert '"implementation_allowed":false' in text
    assert "'live_auto_deploy':False" in text
    assert 'no automatic merge, deployment, capital/risk or live-trading change' in text
