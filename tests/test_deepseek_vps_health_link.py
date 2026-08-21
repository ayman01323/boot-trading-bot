from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deepseek-vps-health-link.yml"


def test_deepseek_vps_health_link_is_inspection_only_and_sanitised() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "DeepSeek VPS Health Link" in text
    assert "runs-on: [self-hosted, linux, x64, boot-vps]" in text
    assert "'.github/deepseek-vps-health-link.trigger'" in text
    assert "sudo /usr/local/sbin/status-boot-trading-bot" in text
    assert "https://api.deepseek.com/chat/completions" in text
    assert "secrets.DEEPSEEK_API_KEY" in text
    assert "deepseek-v4-flash" in text
    assert "vps/deepseek/latest.json" in text
    assert "github_to_vps_link" in text
    assert "inspection_only" in text
    assert "sanitised_context_only" in text
    assert "[REDACTED SENSITIVE LINE]" in text
    assert "provider_http_status" in text
    assert "deepseek_analysis" in text

    for forbidden in (
        "actions/checkout",
        "deploy-boot-trading-bot",
        "pytest",
        "git push",
        "sudo bash",
        "sudo sh",
        "sudo -i",
        "PRIVATE_KEY",
        "--permission-mode",
    ):
        assert forbidden not in text
