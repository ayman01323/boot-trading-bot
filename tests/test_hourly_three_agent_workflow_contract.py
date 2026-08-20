from pathlib import Path


WORKFLOW = Path('.github/workflows/hourly-three-agent-strategy-cycle.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_hourly_strategy_cycle_remains_hourly_and_not_weekly_dependent():
    text = _text()
    assert "cron: '17 * * * *'" in text
    assert 'Weekly GPT Master Corrective Action' not in text


def test_codex_approval_flag_is_global_before_exec_and_uses_exec_api_key():
    text = _text()
    assert 'codex --ask-for-approval never exec --sandbox workspace-write --ephemeral' in text
    assert 'codex exec --sandbox workspace-write --ask-for-approval' not in text
    assert 'CODEX_API_KEY: ${{ secrets.OPENAI_API_KEY }}' in text


def test_missing_runtime_evidence_fails_safe_not_fake_profitability():
    text = _text()
    assert 'MISSING_RUNTIME_FORENSICS' in text
    assert 'do not claim live profitability or canary readiness' in text
    assert 'EVIDENCE_FRESH="false"' in text


def test_provider_failures_are_visible_in_published_reports():
    text = _text()
    assert 'gpt_error.txt' in text
    assert 'gemini_error.txt' in text
    assert 'OpenAI/Codex API credential missing' in text
    assert 'GEMINI_API_KEY missing' in text
    assert 'openai_credential_present' in text
    assert 'gemini_credential_present' in text


def test_copilot_assignment_is_verified_and_never_fake_waiting():
    text = _text()
    assert 'COPILOT_ASSIGN_TOKEN' in text
    assert 'copilot-swe-agent[bot]' in text
    assert "state='BLOCKED_AUTH'" in text
    assert "state='ASSIGNED'" in text
    assert 'BLOCKED_COPILOT_AUTH' in text


def test_strategy_reviews_remain_report_only_and_no_live_auto_deploy():
    text = _text()
    assert 'REPORT ONLY' in text
    assert "'live_auto_deploy':False" in text
    assert 'Do not edit tracked code, trade, deploy, change capital/risk/live settings' in text
