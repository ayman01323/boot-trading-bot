from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


def test_telegram_control_publisher_keeps_five_minute_response_without_repo_checkout_churn() -> None:
    workflow = text('.github/workflows/publish-ai-master-control.yml')
    assert "cron: '*/5 * * * *'" in workflow
    assert 'runs-on: [self-hosted, linux, x64, boot-vps]' in workflow
    assert 'actions: write' in workflow
    assert 'actions/checkout@v4' not in workflow
    assert 'git fetch' not in workflow
    assert 'git worktree' not in workflow
    assert 'Control is semantically unchanged; no ai-reviews commit/write required.' in workflow
    assert "old.pop('published_epoch',None)" in workflow
    assert 'gh api -X PUT "$api"' in workflow
    assert "steps.publish.outputs.changed == 'true'" in workflow
    assert 'gh workflow run claude-vps-controlled-ops.yml' in workflow
    assert '-f action=none' in workflow


def test_claude_vps_idle_polling_and_retry_are_low_bandwidth() -> None:
    controlled = text('.github/workflows/claude-vps-controlled-ops.yml')
    retry = text('.github/workflows/claude-vps-analysis-retry.yml')
    kick = text('.github/workflows/claude-vps-control-kick.yml')
    assert "cron: '13 * * * *'" in controlled
    assert "cron: '*/5 * * * *'" not in controlled
    assert 'fetch-depth: 1' in controlled
    assert 'if ! command -v claude' in controlled
    assert '--max-turns 3' in controlled
    assert 'workflow_run:' not in retry
    assert 'fetch-depth: 1' in retry
    assert 'workflow_run:' not in kick


def test_resource_snapshot_measures_disk_and_host_network_without_checkout_or_secrets() -> None:
    workflow = text('.github/workflows/engineering-vps-resource-snapshot.yml')
    assert "cron: '11 */6 * * *'" in workflow
    assert 'runs-on: [self-hosted, linux, x64, boot-vps]' in workflow
    assert 'actions/checkout' not in workflow
    assert "shutil.disk_usage('/')" in workflow
    assert "open('/proc/net/dev'" in workflow
    assert 'network_megabytes_per_hour' in workflow
    assert 'runner_workspace_bytes' in workflow
    assert 'sanitised_bridge_bytes' in workflow
    assert 'engineering/ops/latest.json' in workflow
    assert 'bandwidth_plan_limit_known' in workflow
    assert 'no_packet_contents' in workflow
    assert 'sudo ' not in workflow
    assert 'ANTHROPIC_API_KEY' not in workflow
    assert 'OPENAI_API_KEY' not in workflow
    assert 'GEMINI_API_KEY' not in workflow
    assert 'PRIVATE_KEY' not in workflow


def test_weekly_engineering_baseline_requires_api_bandwidth_and_disk_review() -> None:
    baseline = text('scripts/weekly_bug_audit_baseline.py')
    assert '"operational_efficiency_audit"' in baseline
    assert '"api_cost"' in baseline
    assert '"server_bandwidth"' in baseline
    assert '"disk_usage"' in baseline
    assert 'paid_ai_provider_signals' in baseline
    assert 'network_download_signals' in baseline
    assert 'checkout_full_history' in baseline
    assert 'latest_cli_installs' in baseline
    assert 'origin/ai-reviews:engineering/ops/latest.json' in baseline
    assert 'must_not_weaken' in baseline
    assert 'advisory_only' in baseline


def test_all_agent_instruction_surfaces_require_operational_efficiency_review() -> None:
    for path in ('AGENTS.md', 'GEMINI.md', 'CLAUDE.md', '.github/copilot-instructions.md'):
        body = text(path).lower()
        assert 'api' in body and 'cost' in body
        assert 'bandwidth' in body
        assert 'disk' in body
        assert 'engineering audit' in body
        assert 'wallet' in body
        assert 'simulation' in body or 'safety' in body
