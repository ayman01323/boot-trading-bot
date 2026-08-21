from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_control_state_has_only_bounded_claude_vps_actions() -> None:
    text = _text("learnerbot/ai_master_control.py")
    assert 'VPS_ACTIONS = ("none", "inspect", "test", "deploy")' in text
    assert '"claude_vps_action_nonce": 0' in text
    assert 'def request_vps_action' in text
    assert 'unsupported Claude VPS action' in text
    assert 'API_KEY' not in text
    assert 'PRIVATE_KEY' not in text


def test_telegram_vps_menu_is_master_only_and_deploy_requires_confirmation() -> None:
    text = _text("learnerbot/telegram_ai_reports_menu_patch.py")
    assert '"🖥 Claude VPS Access"' in text
    assert '"aivps:run:inspect"' in text
    assert '"aivps:run:test"' in text
    assert '"aivps:confirm:deploy"' in text
    assert '"aivps:run:deploy"' in text
    assert '✅ Confirm deploy CURRENT main' in text
    assert 'if not _is_master(app, tid):' in text
    assert 'MASTER only' in text
    assert 'request_vps_action' in text


def test_vps_workflow_uses_only_existing_restricted_sudo_wrappers() -> None:
    text = _text(".github/workflows/claude-vps-controlled-ops.yml")
    assert 'runs-on: [self-hosted, linux, x64, boot-vps]' in text
    assert 'sudo /usr/local/sbin/status-boot-trading-bot' in text
    assert 'sudo /usr/local/sbin/deploy-boot-trading-bot "$target"' in text
    sudo_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("sudo ")]
    assert sudo_lines
    assert all(
        line.startswith("sudo /usr/local/sbin/status-boot-trading-bot")
        or line.startswith("sudo /usr/local/sbin/deploy-boot-trading-bot")
        for line in sudo_lines
    )
    for forbidden in (
        "sudo bash",
        "sudo sh",
        "sudo su",
        "sudo -i",
        "sudo install",
        "useradd",
        "passwd ",
        "chmod 777",
    ):
        assert forbidden not in text


def test_deploy_is_pinned_to_exact_current_origin_main_not_arbitrary_sha() -> None:
    text = _text(".github/workflows/claude-vps-controlled-ops.yml")
    assert 'target="$(git rev-parse origin/main' in text
    assert 'current="$(git rev-parse HEAD' in text
    assert '"$current" == "$target"' in text
    assert 'Refusing deploy: checkout is not exact current origin/main.' in text
    assert 'inputs.sha' not in text
    assert 'TARGET_SHA: ${{ inputs' not in text


def test_claude_runs_on_vps_only_against_redacted_context_in_plan_mode() -> None:
    text = _text(".github/workflows/claude-vps-controlled-ops.yml")
    assert 'ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}' in text
    assert '@anthropic-ai/claude-code@latest' in text
    assert '--permission-mode plan' in text
    assert '--max-turns 1' in text
    assert '[REDACTED SENSITIVE LINE]' in text
    assert 'wallet/private-key access' in text
    assert 'LIVE risk gates' in text


def test_master_prompt_adapter_accepts_only_bounded_vps_fields() -> None:
    text = _text("scripts/resilient_selected_master_v2.py")
    assert 'CLAUDE_VPS_CONTEXT_PATH' in text
    assert 'BOUNDED VPS OPERATIONAL CONTEXT' in text
    assert '"claude_analysis": str(raw.get("claude_analysis")' in text
    assert 'sanitised_action_tail' not in text
    assert 'wallet_or_private_key_access' in text
    assert 'arbitrary_deploy_sha' in text
