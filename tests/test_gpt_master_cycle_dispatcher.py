from pathlib import Path


WORKFLOW = Path('.github/workflows/gpt-master-cycle-dispatcher.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_dispatcher_finds_copilot_pr_by_exact_report_pair_not_mutable_title():
    text = _text()
    assert 'cycle_from_report_pair' in text
    assert ' in:title' not in text
    assert '.ai/strategy/copilot/*.json' in text
    assert '.ai/strategy/copilot/*.md' in text
    assert '[[ ${#files[@]} -eq 2 ]]' in text
    assert 'gpt-master-strategy-action.yml' in text
    assert 'copilot_pr_number="$pr"' in text
    assert 'cycle_id="$cycle"' in text


def test_legacy_gpt_dispatcher_is_manual_only_and_report_pipeline_only():
    text = _text()
    assert 'workflow_dispatch:' in text
    assert 'schedule:' not in text
    assert 'cron:' not in text
    assert 'seven-agent selected MASTER is now authoritative' in text
    assert 'actions: write' in text
    assert 'contents: read' in text
    assert 'pull-requests: read' in text
    assert 'master_decision.json?ref=ai-reviews' in text
    assert '--ref main' in text


def test_dispatcher_scopes_pre_checkout_gh_commands_to_repository():
    text = _text()
    dispatch_step = text.split(
        '- name: Find an unresolved exact Copilot report pair and dispatch legacy GPT Master', 1
    )[1]
    assert 'GH_REPO: ${{ github.repository }}' in dispatch_step
    assert 'gh pr list' in dispatch_step
    assert 'gh workflow run gpt-master-strategy-action.yml' in dispatch_step


def test_dispatcher_scans_unresolved_cycles_instead_of_only_latest_pointer():
    text = _text()
    assert 'gh pr list --state open --limit 100' in text
    assert 'master_decision.json?ref=ai-reviews' in text
    assert 'latest_cycle_id.txt' not in text
    assert 'isCrossRepository' in text
