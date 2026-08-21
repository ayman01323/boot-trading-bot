from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_kick_workflow_only_dispatches_bounded_processor() -> None:
    text = (ROOT / ".github/workflows/claude-vps-control-kick.yml").read_text(encoding="utf-8")
    assert 'workflows:' in text
    assert '"Publish Telegram AI Master Control"' in text
    assert 'actions: write' in text
    assert 'contents: read' in text
    assert 'gh workflow run claude-vps-controlled-ops.yml' in text
    assert '-f action=none' in text
    assert 'runs-on: ubuntu-latest' in text

    # The kick workflow cannot itself inspect, test, deploy, use sudo, choose a SHA,
    # or reach wallet/signing material. It only wakes the already bounded processor.
    forbidden = (
        'sudo ',
        'deploy-boot-trading-bot',
        'status-boot-trading-bot',
        'ANTHROPIC_API_KEY',
        'PRIVATE_KEY',
        'mnemonic',
        'seed phrase',
        'wallet',
        '-f action=inspect',
        '-f action=test',
        '-f action=deploy',
    )
    for value in forbidden:
        assert value not in text
