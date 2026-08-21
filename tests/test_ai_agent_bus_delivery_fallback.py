from pathlib import Path


WORKFLOW = Path('.github/workflows/ai-agent-bus.yml')


def _text() -> str:
    return WORKFLOW.read_text(encoding='utf-8')


def test_bus_keeps_instant_issue_comment_trigger_and_adds_polling_fallback() -> None:
    text = _text()
    assert 'issue_comment:' in text
    assert "cron: '*/5 * * * *'" in text
    assert 'workflow_dispatch:' in text


def test_bus_serialises_runs_and_discovers_unanswered_messages() -> None:
    text = _text()
    assert 'group: ai-agent-bus' in text
    assert 'Find latest unanswered AI bus message' in text
    assert 'AI_BUS_REPLY' in text
    assert 'replied.add(message_id)' in text
    assert 'pending = [row for row in requests if row[1] not in replied]' in text


def test_bus_rechecks_before_posting_to_prevent_duplicate_replies() -> None:
    text = _text()
    assert 'Re-check immediately before publishing' in text
    assert 'Reply already exists for' in text
    assert 'skipping duplicate publish' in text


def test_provider_call_remains_bounded_and_reply_only() -> None:
    text = _text()
    assert 'python scripts/ai_agent_bus.py' in text
    assert 'ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}' in text
    assert 'issues: write' in text
    assert 'contents: read' in text
    assert 'contents: write' not in text
