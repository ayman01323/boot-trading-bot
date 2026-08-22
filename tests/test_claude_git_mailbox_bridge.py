from __future__ import annotations

import base64
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'scripts' / 'claude_git_mailbox_bridge.py'
SPEC = importlib.util.spec_from_file_location('claude_git_mailbox_bridge', MODULE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


def claude_message(message_id: str = 'claude-1') -> str:
    return (
        'CLAUDE_TO_GPT\n'
        f'message_id: {message_id}\n'
        'source_sha: abc123\n'
        'status: REQUEST\n'
        'constraints: READ_ONLY; no secrets\n\n'
        'Please review this bounded message.\n'
    )


def bus_reply(message_id: str = 'claude-1') -> str:
    return (
        'AI_BUS_REPLY\n'
        f'message_id: {message_id}\n'
        'from: BUS\n'
        'to: CLAUDE\n'
        'status: COMPLETED\n'
        'mode: DIRECT\n'
        'provider_calls: 1\n'
        'max_hops: 1\n\n'
        'GPT received the message.\n'
    )


def test_normalize_routes_only_to_gpt() -> None:
    message_id, envelope = bridge.normalize_claude_message(claude_message())
    assert message_id == 'claude-1'
    assert envelope.startswith('AI_BUS\n')
    assert 'from: CLAUDE\n' in envelope
    assert 'to: GPT\n' in envelope
    assert 'mode: DIRECT\n' in envelope
    assert 'max_hops: 1\n' in envelope


def test_invalid_prefix_or_message_id_is_rejected() -> None:
    for text in ('NOT_CLAUDE\nmessage_id: x\n', 'CLAUDE_TO_GPT\nmessage_id: bad id\n'):
        try:
            bridge.normalize_claude_message(text)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid mailbox input was accepted')


def test_existing_reply_dedupes_exact_message(monkeypatch) -> None:
    incoming = claude_message('done-1')
    outgoing = (
        'GPT_TO_CLAUDE\n'
        'in_reply_to: done-1\n'
        'status: COMPLETED\n\n'
        'already handled\n'
    )

    def fake_fetch(repo: str, path: str, *, token: str):
        return (incoming, 'in-sha') if path == bridge.CLAUDE_TO_GPT_PATH else (outgoing, 'out-sha')

    monkeypatch.setattr(bridge, 'fetch_fixed_file', fake_fetch)
    pending, message_id, _ = bridge.select_pending('owner/repo', token='t')
    assert pending is False
    assert message_id == 'done-1'


def test_new_message_is_pending(monkeypatch) -> None:
    incoming = claude_message('new-1')
    outgoing = 'GPT_TO_CLAUDE\nin_reply_to: older\nstatus: COMPLETED\n\nold\n'

    def fake_fetch(repo: str, path: str, *, token: str):
        return (incoming, 'in-sha') if path == bridge.CLAUDE_TO_GPT_PATH else (outgoing, 'out-sha')

    monkeypatch.setattr(bridge, 'fetch_fixed_file', fake_fetch)
    pending, message_id, envelope = bridge.select_pending('owner/repo', token='t')
    assert pending is True
    assert message_id == 'new-1'
    assert 'to: GPT\n' in envelope


def test_publish_writes_only_fixed_reply_path(monkeypatch) -> None:
    calls = []

    def fake_fetch(repo: str, path: str, *, token: str):
        assert path == bridge.GPT_TO_CLAUDE_PATH
        return ('GPT_TO_CLAUDE\nin_reply_to: old\nstatus: COMPLETED\n', 'old-sha')

    def fake_json(url: str, *, token: str, method: str = 'GET', payload=None):
        calls.append((url, method, payload))
        return {}

    monkeypatch.setattr(bridge, 'fetch_fixed_file', fake_fetch)
    monkeypatch.setattr(bridge, '_github_json', fake_json)
    bridge.publish_reply('owner/repo', token='t', message_id='claude-1', bus_reply=bus_reply())
    assert len(calls) == 1
    url, method, payload = calls[0]
    assert url.endswith('/contents/.github/ai-mailbox/gpt-to-claude.md')
    assert method == 'PUT'
    assert payload['branch'] == 'ai-mailbox'
    assert payload['sha'] == 'old-sha'
    decoded = base64.b64decode(payload['content']).decode()
    assert decoded.startswith('GPT_TO_CLAUDE\n')
    assert 'in_reply_to: claude-1\n' in decoded


def test_arbitrary_mailbox_path_is_rejected() -> None:
    try:
        bridge.fetch_fixed_file('owner/repo', '.env', token='t')
    except ValueError as exc:
        assert 'not allowed' in str(exc)
    else:
        raise AssertionError('arbitrary path was accepted')
