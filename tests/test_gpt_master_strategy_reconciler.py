from pathlib import Path


WORKFLOW = Path('.github/workflows/gpt-master-strategy-action.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_master_reconciler_runs_from_default_branch_schedule_not_bot_pr_event():
    text = _text()
    assert "cron: '*/10 * * * *'" in text
    assert 'workflow_dispatch:' in text
    assert '\n  pull_request:' not in text
    assert 'cycle_from_report_pair' in text
    assert 'No unresolved PR with an exact Copilot report pair is ready' in text


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

def test_master_scopes_pre_checkout_gh_pr_commands_to_repository():
    text = _text()
    resolve_step = text.split(
        '- name: Resolve completed Copilot report PR by immutable cycle', 1
    )[1].split('- name: Check out Copilot report PR', 1)[0]
    assert 'GH_REPO: ${{ github.repository }}' in resolve_step
    assert 'gh pr list' in resolve_step
    assert 'gh pr diff "$candidate" --name-only' in resolve_step
    assert resolve_step.count('gh pr view') >= 2


def test_master_pins_explicit_pr_to_its_report_cycle_not_latest_pointer():
    text = _text()
    resolve_step = text.split(
        '- name: Resolve completed Copilot report PR by immutable cycle', 1
    )[1].split('- name: Check out Copilot report PR', 1)[0]
    assert 'INPUT_CYCLE: ${{ inputs.cycle_id }}' in resolve_step
    assert 'cycle="$(cycle_from_report_pair "$pr" "$cycle")"' in resolve_step
    assert 'latest_cycle_id.txt' not in resolve_step
    assert '.ai/strategy/copilot/*.json' in resolve_step
    assert '.ai/strategy/copilot/*.md' in resolve_step
    assert '[[ ${#files[@]} -eq 2 ]]' in resolve_step
