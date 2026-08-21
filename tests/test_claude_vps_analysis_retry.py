from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_claude_vps_analysis_retry_is_read_only_and_bounded() -> None:
    text = (ROOT / '.github/workflows/claude-vps-analysis-retry.yml').read_text(encoding='utf-8')
    assert 'runs-on: [self-hosted, linux, x64, boot-vps]' in text
    assert 'sudo /usr/local/sbin/status-boot-trading-bot' in text
    assert '--permission-mode plan' in text
    assert '--max-turns 3' in text
    assert 'Do not use tools; answer from the supplied context only.' in text
    assert 'claude_analysis_available' in text
    assert 'claude_analysis_turn_limit' in text
    assert "d.get('action')=='inspect'" in text
    assert "d.get('status')=='SUCCESS'" in text
    assert '[REDACTED SENSITIVE LINE]' in text

    sudo_lines = [line.strip() for line in text.splitlines() if line.strip().startswith('sudo ')]
    # Tolerate a non-zero status-check exit (matches deploy-vps.yml's pattern) so a
    # transient status-wrapper failure can't silently kill the job before it ever
    # reaches the Claude retry or publish steps.
    assert sudo_lines == ['sudo /usr/local/sbin/status-boot-trading-bot > /tmp/claude-vps-retry-status.txt 2>&1 || true']

    for forbidden in (
        'deploy-boot-trading-bot',
        '-f action=deploy',
        'PRIVATE_KEY',
        '/root/.ssh',
        'id_rsa',
        'WALLET_PRIVATE_KEY',
        'chmod 777',
        'sudo bash',
        'sudo sh',
    ):
        assert forbidden not in text
