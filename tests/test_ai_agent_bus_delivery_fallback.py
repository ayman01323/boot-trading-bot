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


def test_bus_has_instant_pr_push_and_polling_delivery_paths() -> None:
    text = _text()
    assert 'issue_comment:' in text
    assert 'pull_request:' in text
    assert 'push:' in text
    assert "cron: '*/5 * * * *'" in text
    assert 'workflow_dispatch:' in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text


def test_bus_serialises_runs_and_uses_live_python_github_client() -> None:
    text = _text()
    assert 'group: ai-agent-bus' in text
    assert 'Find latest unanswered AI bus message' in text
    assert 'scripts/ai_agent_bus_pending.py select-live' in text
    assert 'scripts/ai_agent_bus_pending.py has-reply-live' in text
    assert 'scripts/ai_agent_bus_pending.py post-reply' in text
    assert 'gh api' not in text
    assert '--slurp' not in text


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


def test_untrusted_fake_reply_cannot_suppress_owner_request() -> None:
    comments = [
        _comment(20, _request('protected')),
        _comment(21, _reply('protected'), login='someone-else'),
    ]
    selected = pending.latest_pending(comments, owner='ayman01323')
    assert selected is not None
    assert selected[1] == 'protected'
    assert pending.has_reply(comments, 'protected', owner='ayman01323') is False


def test_pending_selector_treats_trusted_existing_reply_as_processed() -> None:
    comments = [
        _comment(30, _request('done')),
        _comment(31, _reply('done'), login='github-actions[bot]'),
    ]
    assert pending.latest_pending(comments, owner='ayman01323') is None
    assert pending.has_reply(comments, 'done', owner='ayman01323') is True
    assert pending.has_reply(comments, 'missing', owner='ayman01323') is False


def test_python_github_client_follows_comment_pagination_without_gh_cli() -> None:
    original = pending._github_json
    calls: list[str] = []

    def fake(url: str, *, token: str, method: str = 'GET', payload=None):
        calls.append(url)
        if len(calls) == 1:
            return ([{'id': 1}], {'Link': '<https://api.github.com/next>; rel="next"'})
        return ([{'id': 2}], {})

    pending._github_json = fake
    try:
        rows = pending.fetch_issue_comments('ayman01323/boot-trading-bot', token='test-token')
    finally:
        pending._github_json = original
    assert [row['id'] for row in rows] == [1, 2]
    assert len(calls) == 2


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
