from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deepseek-github-readonly-selfhosted.yml"


def test_readonly_deepseek_inspector_is_bounded_and_telegram_compatible() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "DeepSeek GitHub Read-Only System Inspect" in text
    assert "runs-on: [self-hosted, linux, x64, boot-vps]" in text
    assert "'.github/deepseek-readonly-inspect.trigger'" in text
    assert "ref: main" in text
    assert "persist-credentials: false" in text
    assert "PYTHONDONTWRITEBYTECODE=1" in text
    assert '"$DEEPSEEK_RO_VENV/bin/python" -m pytest -q' in text

    # The read-only inspector uses the same direct DeepSeek HTTP provider path
    # already used by the live AI Council, and deliberately ignores the VPS
    # runtime credential file in favour of the masked Actions secret.
    assert "secrets.DEEPSEEK_API_KEY" in text
    assert "AI_COUNCIL_RUNTIME_ENV: /tmp/deepseek-readonly-no-runtime.env" in text
    assert "from learnerbot.ai_council_http_patch import call_provider" in text
    assert "call_provider('deepseek', prompt)" in text

    # Telegram reads this same ai-reviews result file.
    assert "github/deepseek/latest.json" in text
    assert "'provider': 'deepseek'" in text
    assert "'action': 'inspect'" in text
    assert "'test_pass_count': pass_count" in text
    assert "'context_only': True" in text
    assert "'repository_editing': False" in text
    assert "'self_merge': False" in text
    assert "'deploy_from_github_workflow': False" in text
    assert "'wallet_or_private_key_access': False" in text
    assert "'live_or_risk_bypass': False" in text

    for forbidden in (
        "sudo ",
        "gh pr create",
        "gh pr merge",
        "git push origin main",
        "deploy-boot-trading-bot",
        "--permission-mode acceptEdits",
        "subprocess.",
        "os.system(",
    ):
        assert forbidden not in text
