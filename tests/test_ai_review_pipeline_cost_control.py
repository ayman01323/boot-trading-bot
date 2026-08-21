from pathlib import Path


WORKFLOW = Path('.github/workflows/ai-review-pipeline-ci.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_pr_pipeline_is_static_and_has_no_provider_secrets_or_cli_calls():
    text = _text()
    start = text.index('  pipeline:')
    end = text.index('  provider_smoke:')
    pipeline = text[start:end]
    assert 'CODEX_API_KEY' not in pipeline
    assert 'GEMINI_API_KEY' not in pipeline
    assert 'codex --ask-for-approval' not in pipeline
    assert 'gemini --approval-mode' not in pipeline
    assert 'npm install -g @openai/codex' not in pipeline
    assert 'no paid model provider was called' in pipeline


def test_live_provider_smoke_is_manual_opt_in_only():
    text = _text()
    assert 'live_provider_check:' in text
    assert 'default: false' in text
    provider = text[text.index('  provider_smoke:'):text.index('  agent_recovery:')]
    assert "if: github.event_name == 'workflow_dispatch' && inputs.live_provider_check == true" in provider
    assert 'CODEX_API_KEY' in provider
    assert 'GEMINI_API_KEY' in provider


def test_expensive_agent_recovery_review_is_manual_opt_in_only():
    text = _text()
    assert 'agent_recovery_review:' in text
    recovery = text[text.index('  agent_recovery:'):]
    assert "if: github.event_name == 'workflow_dispatch' && inputs.agent_recovery_review == true" in recovery
    assert 'CODEX_API_KEY' in recovery
    assert 'GEMINI_API_KEY' in recovery
