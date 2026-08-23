import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_solana_position_lookup_wrapper.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "solana-position-lookup.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_installer_shell_syntax_is_valid():
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)


def test_wrapper_is_fixed_no_argument_and_strict_position_id():
    text = _text(INSTALLER)
    assert 'LOOKUP_WRAPPER="/usr/local/sbin/lookup-solana-sibot-position"' in text
    assert r"if [[ \$# -ne 0 ]]" in text
    assert r"^[0-9a-f]{32}\$" in text
    assert "Missing position id on stdin" in text
    assert "$RUNNER_USER ALL=(root) NOPASSWD: $LOOKUP_WRAPPER" in text
    assert "NOPASSWD: ALL" not in text


def test_wrapper_queries_only_fixed_readonly_solana_position_fields():
    text = _text(INSTALLER)
    assert 'DB="$BOT_DIR/data/solana_sibot.sqlite3"' in text
    assert "?mode=ro" in text
    assert 'conn.execute("PRAGMA query_only=ON")' in text
    assert "FROM positions" in text
    assert "WHERE position_id = ?" in text
    assert "(position_id,)" in text
    assert "telegram_id" not in text
    assert "leader_wallet" not in text
    assert "leader_buy_signature" not in text
    assert "exit_signature" not in text


def test_workflow_never_reads_root_database_directly():
    text = _text(WORKFLOW)
    assert 'LOOKUP_WRAPPER: /usr/local/sbin/lookup-solana-sibot-position' in text
    assert 'sudo -n "$LOOKUP_WRAPPER" < /tmp/solana-position-id.txt' in text
    assert "/root/multichain-learning-bot-v2.2-fast-direct-market" not in text
    assert "solana_sibot.sqlite3" not in text
    assert "persist-credentials: false" in text


def test_workflow_supports_owner_only_issue_bus_trigger_and_manual_dispatch():
    text = _text(WORKFLOW)
    assert "workflow_dispatch:" in text
    assert "issue_comment:" in text
    assert "github.event.issue.number == 333" in text
    assert "github.event.comment.user.login == github.repository_owner" in text
    assert "startsWith(github.event.comment.body, 'POSITION_LOOKUP ')" in text
    assert "POSITION_LOOKUP_REPLY" in text
