import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "run-sibot-leader-gate-report.yml"
REPORT = ROOT / "scripts" / "sibot_leader_gate_report.py"
INSTALLER = ROOT / "scripts" / "install_sibot_leader_gate_wrapper.sh"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_installer_shell_syntax_is_valid():
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)


def test_report_requires_isolated_snapshot():
    text = _text(REPORT)
    assert 'os.getenv("SIBOT_GATE_SNAPSHOT") != "1"' in text
    assert "Refusing to run SiBot leader-gate report outside the isolated snapshot" in text


def test_report_is_low_memory_sqlite_only_and_has_no_provider_calls():
    text = _text(REPORT)
    assert "LOW_MEMORY_SQLITE_ONLY" in text
    assert "provider_calls: 0" in text
    assert "CANDIDATE_CAP = 5" in text
    assert "PROOF_CHAIN_IDS = {56, 42161}" in text
    assert "import requests" not in text
    assert "from web3" not in text.lower()
    assert "_load_patch_chain" not in text
    assert "learnerbot." not in text


def test_report_forces_read_only_sqlite():
    text = _text(REPORT)
    assert "?mode=ro" in text
    assert "PRAGMA query_only=ON" in text
    assert "PRAGMA busy_timeout=30000" in text
    assert "sqlite3.connect" in text


def test_report_emits_explicit_bounded_scope_metadata():
    text = _text(REPORT)
    assert "eligible_candidates=" in text
    assert "processed_candidates=" in text
    assert "BOUNDED_PROOF_RESULT=" in text
    assert "candidate_reconstruction_any=" in text
    assert "store_reconstruction_any=" in text


def test_report_keeps_final_quality_floors_visible_in_code():
    text = _text(REPORT)
    assert '"min_closed_trades": Decimal("50")' in text
    assert '"min_win_rate_pct": Decimal("55")' in text
    assert '"min_profit_factor": Decimal("1.5")' in text
    assert '"min_recent_win_rate_pct": Decimal("55")' in text
    assert '"min_recent_profit_factor": Decimal("1.10")' in text
    assert '"max_leader_drawdown_pct": Decimal("20")' in text
    assert '"min_win_rate_pct": Decimal("65")' in text
    assert '"min_profit_factor": Decimal("1.75")' in text


def test_root_wrapper_is_fixed_no_argument_and_refuses_dirty_non_main_code():
    text = _text(INSTALLER)
    assert 'REPORT_WRAPPER="/usr/local/sbin/run-sibot-leader-gate-report"' in text
    assert r"if [[ \$# -ne 0 ]]" in text
    assert "git branch --show-current" in text
    assert '!= "main"' in text
    assert "git diff --quiet" in text
    assert "git diff --cached --quiet" in text
    assert "git ls-files --error-unmatch" in text


def test_root_wrapper_uses_isolated_code_config_and_database_snapshots():
    text = _text(INSTALLER)
    assert "git archive --format=tar HEAD" in text
    assert r'cp -a "\$CSV_DIR" "\$SNAPSHOT/CSVbot"' in text
    assert "source.backup(destination)" in text
    assert "?mode=ro" in text
    assert "SIBOT_GATE_SNAPSHOT=1" in text
    assert r'BOOT_GIT_SHA="\$DEPLOYED_SHA"' in text
    assert "env -i" in text
    assert r'"\$SNAPSHOT/\$REPORT_SCRIPT"' in text


def test_sudoers_grants_only_the_exact_report_wrapper():
    text = _text(INSTALLER)
    line = "$RUNNER_USER ALL=(root) NOPASSWD: $REPORT_WRAPPER"
    assert line in text
    assert "$RUNNER_USER ALL=(root) NOPASSWD: $REPORT_WRAPPER *" not in text
    assert "NOPASSWD: ALL" not in text


def test_workflow_uses_restricted_wrapper_not_direct_root_paths_or_branch_copy():
    text = _text(WORKFLOW)
    assert 'sudo -n "$REPORT_WRAPPER"' in text
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
