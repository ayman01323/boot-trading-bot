from pathlib import Path


WORKFLOW = Path('.github/workflows/gpt-master-strategy-action.yml')
SELECTED = Path('.github/workflows/selected-ai-master.yml')
RUNNER = Path('scripts/resilient_selected_master_v2.py')


def _text(path=WORKFLOW):
    return Path(path).read_text(encoding='utf-8')


def test_legacy_gpt_master_is_only_a_manual_default_branch_compatibility_delegate():
    text = _text()
    assert 'workflow_dispatch:' in text
    assert 'schedule:' not in text
    assert 'cron:' not in text
    assert '\n  pull_request:' not in text
    assert 'selected-ai-master.yml' in text
    assert 'lane=strategy' in text
    assert 'GPT is now one selectable/fallback MASTER' in text


def test_selected_master_checks_out_main_without_persisted_credentials():
    text = _text(SELECTED)
    assert 'ref: main' in text
    assert 'persist-credentials: false' in text
    assert '@openai/codex@latest' in text
    assert '@anthropic-ai/claude-code@latest' in text
    assert '@google/gemini-cli@latest' in text
    assert '@github/copilot@latest' in text


def test_selected_master_does_not_require_all_agents_complete():
    text = _text(SELECTED)
    runner = _text('scripts/resilient_selected_master.py')
    assert 'Collected ${count} candidate report file(s)' in text
    assert 'if [[ "$count" == 0 ]]' in text
    assert '"minimum_valid_reports_to_continue": 1' in runner
    assert 'failed_agent_count' in runner
    assert 'resilient_cycle_continued' in runner


def test_selected_master_is_never_direct_live_trading_authority():
    runner = _text('scripts/resilient_selected_master.py')
    assert '"live_auto_deploy": False' in runner
    assert '"live_trading_depends_on_ai_health": False' in runner
    assert 'No AI report or master decision may directly trade' in runner
    assert 'wallet/signing' in runner


def test_selected_master_uses_exact_requested_seven_agent_fallback_priority():
    text = _text(RUNNER)
    assert '_FALLBACK = ("gpt", "claude", "gemini", "deepseek", "grok", "kimi", "copilot")' in text
    assert 'if preferred in _base.PROVIDERS' in text
    assert 'if provider not in out' in text
    assert '"--plan"' in text
    assert 'DEEPSEEK_API_KEY' in text
    assert 'KIMI_API_KEY' in text


def test_selected_master_collects_copilot_report_from_exact_cycle_pr_when_needed():
    text = _text(SELECTED)
    assert '.ai/strategy/copilot/${identity}.json' in text
    assert '.ai/weekly/copilot/${identity}.json' in text
    assert 'gh pr diff "$number" --name-only' in text
    assert 'ref=${head}' in text
