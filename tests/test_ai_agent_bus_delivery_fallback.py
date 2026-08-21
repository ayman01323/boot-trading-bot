from pathlib import Path

from scripts import ai_agent_bus_pending as pending


WORKFLOW = Path('.github/workflows/ai-agent-bus.yml')


def _text() -> str:
    return WORKFLOW.read_text(encoding='utf-8')


def _comment(comment_id: int, body: str, login: str = 'ayman01323') -> dict:
    return {'id': comment_id, 'body': body, 'user': {'login': login}}


def _request(message_id: str) -> str:
    return (
        'AI_BUS\n'
        f'message_id: {message_id}\n'
        'from: GPT\n'
        'to: CLAUDE\n'
        'mode: DIRECT\n'
        'max_hops: 1\n\n'
        'Ping Claude.\n'
    )


def _reply(message_id: str) -> str:
    return (
        'AI_BUS_REPLY\n'
        f'message_id: {message_id}\n'
        'from: BUS\n'
        'to: GPT\n'
        'status: COMPLETED\n\n'
        'Claude replied.\n'
    )


def test_bus_keeps_instant_issue_comment_trigger_and_adds_polling_fallback() -> None:
    text = _text()
    assert 'issue_comment:' in text
    assert "cron: '*/5 * * * *'" in text
    assert 'workflow_dispatch:' in text


def test_bus_serialises_runs_and_uses_deterministic_pending_helper() -> None:
    text = _text()
    assert 'group: ai-agent-bus' in text
    assert 'Find latest unanswered AI bus message' in text
    assert 'scripts/ai_agent_bus_pending.py select' in text
    assert 'scripts/ai_agent_bus_pending.py has-reply' in text


def test_pending_selector_chooses_latest_unanswered_trusted_request() -> None:
    comments = [
        _comment(10, _request('old')),
        _comment(11, _reply('old'), login='github-actions[bot]'),
        _comment(12, _request('new')),
        _comment(13, _request('untrusted'), login='someone-else'),
    ]
    selected = pending.latest_pending(comments, owner='ayman01323')
    assert selected is not None
    comment_id, message_id, body = selected
    assert comment_id == 12
    assert message_id == 'new'
    assert body.startswith('AI_BUS\n')


def test_pending_selector_treats_existing_reply_as_processed() -> None:
    comments = [
        _comment(20, _request('done')),
        _comment(21, _reply('done'), login='github-actions[bot]'),
    ]
    assert pending.latest_pending(comments, owner='ayman01323') is None
    assert pending.has_reply(comments, 'done') is True
    assert pending.has_reply(comments, 'missing') is False


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
