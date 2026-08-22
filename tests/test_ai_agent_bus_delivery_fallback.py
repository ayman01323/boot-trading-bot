from pathlib import Path

from scripts import ai_agent_bus_pending as pending


WORKFLOW = Path('.github/workflows/ai-agent-bus.yml')
COMPAT = Path('scripts/ai_agent_bus_provider_compat.py')
LAUNCHER = Path('scripts/run_ai_agent_bus.py')


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


def _legacy_claude_request(message_id: str) -> str:
    return (
        'CLAUDE_TO_GPT\n'
        f'message_id: {message_id}\n'
        'source_sha: abc123\n'
        'request: Check the latest bounded report.\n'
        'constraints: Read-only.\n'
        'evidence: run 123.\n\n'
        'Please reply with the result.\n'
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


def test_bus_is_event_driven_without_scheduled_polling() -> None:
    text = _text()
    assert 'issue_comment:' in text
    assert 'pull_request:' in text
    assert 'push:' in text
    assert 'schedule:' not in text
    assert "cron: '*/5 * * * *'" not in text
    assert 'workflow_dispatch:' in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text


def test_bus_uses_known_good_self_hosted_runner_and_native_isolated_python() -> None:
    text = _text()
    assert 'runs-on: [self-hosted, linux, x64, boot-vps]' in text
    assert 'python3 -m venv "$BUS_VENV"' in text
    assert 'PYTHONPATH: ${{ github.workspace }}' in text
    assert 'actions/setup-python' not in text
    assert '"$BUS_VENV/bin/python" scripts/run_ai_agent_bus.py' in text


def test_bus_uses_standalone_copilot_only_when_needed() -> None:
    text = _text()
    assert 'curl -fsSL https://gh.io/copilot-install' in text
    assert 'PREFIX="$prefix" bash' in text
    assert '"$prefix/bin/copilot" --version' in text
    assert 'npm install -g @github/copilot' not in text
    assert 'actions/setup-node' not in text


def test_bus_serialises_runs_and_uses_live_python_github_client() -> None:
    text = _text()
    assert 'group: ai-agent-bus' in text
    assert 'Find latest unanswered AI bus message' in text
    assert 'scripts/ai_agent_bus_pending.py select-live' in text
    assert 'scripts/ai_agent_bus_pending.py has-reply-live' in text
    assert 'scripts/ai_agent_bus_pending.py post-reply' in text
    assert text.count('gh api') == 2
    assert 'api="repos/${GITHUB_REPOSITORY}/contents/github/ai-agent-bus/latest.json"' in text
    assert "'branch': 'ai-reviews'" in text
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


def test_legacy_claude_to_gpt_message_is_automatically_normalized_and_routed() -> None:
    selected = pending.latest_pending(
        [_comment(40, _legacy_claude_request('claude-inbound-1'))],
        owner='ayman01323',
    )
    assert selected is not None
    comment_id, message_id, body = selected
    assert comment_id == 40
    assert message_id == 'claude-inbound-1'
    assert body.startswith('AI_BUS\n')
    assert 'from: CLAUDE\n' in body
    assert 'to: GPT\n' in body
    assert 'mode: DIRECT\n' in body
    assert 'Legacy Claude-to-GPT mailbox message received automatically.' in body
    assert 'request: Check the latest bounded report.' in body


def test_legacy_claude_to_gpt_message_respects_existing_reply_dedupe() -> None:
    comments = [
        _comment(50, _legacy_claude_request('claude-done')),
        _comment(51, _reply('claude-done'), login='github-actions[bot]'),
    ]
    assert pending.latest_pending(comments, owner='ayman01323') is None


def test_untrusted_legacy_claude_message_is_ignored() -> None:
    comments = [_comment(60, _legacy_claude_request('fake-claude'), login='someone-else')]
    assert pending.latest_pending(comments, owner='ayman01323') is None


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
    assert 'Reply already exists for' in text
    assert 'skipping duplicate publish' in text


def test_claude_bus_compatibility_omits_deprecated_temperature() -> None:
    text = COMPAT.read_text(encoding='utf-8')
    assert '_call_claude_without_deprecated_temperature' in text
    assert '"model": model' in text
    assert '"max_tokens": 2400' in text
    assert '"messages": [{"role": "user", "content": prompt}]' in text
    assert '"temperature":' not in text
    assert 'return _http.call_provider(provider, prompt)' in text


def test_bus_launcher_installs_compatibility_before_main() -> None:
    text = LAUNCHER.read_text(encoding='utf-8')
    assert 'from scripts.ai_agent_bus_provider_compat import install' in text
    assert 'install()' in text
    assert 'from scripts.ai_agent_bus import main' in text


def test_provider_call_remains_bounded_and_reply_only() -> None:
    text = _text()
    assert 'ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}' in text
    assert 'issues: write' in text
    assert 'contents: write' in text
    assert 'Publish sanitised reply snapshot for MASTER Telegram alerts' in text
    assert 'api="repos/${GITHUB_REPOSITORY}/contents/github/ai-agent-bus/latest.json"' in text
    assert "'branch': 'ai-reviews'" in text
    assert 'sudo ' not in text
    assert 'deploy-boot-trading-bot' not in text
