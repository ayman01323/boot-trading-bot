from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "run-sibot-leader-gate-report.yml"
REPORT = ROOT / "scripts" / "sibot_leader_gate_report.py"
INSTALLER = ROOT / "scripts" / "install_sibot_leader_gate_wrapper.sh"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_report_requires_snapshot_and_never_loads_dotenv_secrets():
    text = _text(REPORT)
    assert 'os.getenv("SIBOT_GATE_SNAPSHOT") != "1"' in text
    assert "Refusing to run SiBot leader-gate report outside the isolated snapshot" in text
    assert "dotenv.load_dotenv = lambda" in text


def test_report_forces_read_only_sqlite_and_disables_settings_migrations():
    text = _text(REPORT)
    assert "?mode=ro" in text
    assert 'PRAGMA query_only=ON' in text
    assert "_sibot.connect = lambda app: _readonly_sqlite" in text
    assert "_sol.connect = lambda app: _readonly_sqlite" in text
    assert "_sibot.ensure_settings = sibot_settings_path" in text
    assert "_sol.ensure_settings = solana_settings_path" in text
    assert "_sibot._atomic_csv = _blocked_config_write" in text
    assert text.count("_install_readonly_guards()") >= 2


def test_report_does_not_silently_skip_uppercase_evm_chain_types():
    text = _text(REPORT)
    assert 'str(chain.type).strip().lower() != "evm"' in text
    assert 'chain.type != "evm"' not in text


def test_root_wrapper_is_fixed_no_argument_and_refuses_dirty_non_main_code():
    text = _text(INSTALLER)
    assert 'REPORT_WRAPPER="/usr/local/sbin/run-sibot-leader-gate-report"' in text
    assert "if [[ \\$# -ne 0 ]]" in text
    assert 'git branch --show-current' in text
    assert '!= "main"' in text
    assert "git diff --quiet" in text
    assert "git diff --cached --quiet" in text
    assert 'git ls-files --error-unmatch' in text


def test_root_wrapper_uses_isolated_code_config_and_database_snapshots():
    text = _text(INSTALLER)
    assert "git archive --format=tar HEAD" in text
    assert 'cp -a "\\$CSV_DIR" "\\$SNAPSHOT/CSVbot"' in text
    assert "source.backup(destination)" in text
    assert "?mode=ro" in text
    assert "SIBOT_GATE_SNAPSHOT=1" in text
    assert "env -i" in text
    assert '"\\$SNAPSHOT/\\$REPORT_SCRIPT"' in text


def test_sudoers_grants_only_the_exact_report_wrapper():
    text = _text(INSTALLER)
    line = '$RUNNER_USER ALL=(root) NOPASSWD: $REPORT_WRAPPER'
    assert line in text
    assert '$RUNNER_USER ALL=(root) NOPASSWD: $REPORT_WRAPPER *' not in text
    assert "NOPASSWD: ALL" not in text


def test_workflow_uses_restricted_wrapper_not_direct_root_paths_or_branch_copy():
    text = _text(WORKFLOW)
    assert "sudo -n \"$REPORT_WRAPPER\"" in text
    assert "/root/multichain-learning-bot-v2.2-fast-direct-market/data" not in text
    assert "/root/multichain-learning-bot-v2.2-fast-direct-market/CSVbot" not in text
    assert "claude/restore-viable-leader-thresholds" not in text
    assert "leader-report-source" not in text
    assert "persist-credentials: false" in text


def test_workflow_records_deployed_vs_current_main_sha():
    text = _text(WORKFLOW)
    assert "current_main_sha" in text
    assert "deployed_sha" in text
    assert "deployed_matches_current_main" in text
