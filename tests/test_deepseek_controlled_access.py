from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_deepseek_github_access_is_draft_pr_only_and_protected() -> None:
    text = _text(".github/workflows/deepseek-github-controlled-ops.yml")
    assert "DeepSeek GitHub Controlled Operations" in text
    assert "workflow_dispatch:" in text
    assert "contents: write" in text
    assert "pull-requests: write" in text
    assert "issues: write" in text
    assert "secrets.DEEPSEEK_API_KEY" in text
    assert "https://api.deepseek.com/anthropic" in text
    assert "--permission-mode plan" in text
    assert "--permission-mode acceptEdits" in text
    assert "gh pr create --draft" in text
    assert "[DEEPSEEK]" in text
    assert "AGENT: DEEPSEEK" in text
    assert "AI-Agent: DEEPSEEK" in text
    assert "pytest -q" in text
    assert "Protected DeepSeek changes refused" in text
    assert ".github/" in text
    assert "learnerbot/wallet" in text
    assert "learnerbot/live_executor" in text
    assert "learnerbot/solana_live" in text
    for forbidden in ("gh pr merge", "git push origin main", "sudo ", "deploy-boot-trading-bot"):
        assert forbidden not in text


def test_deepseek_vps_access_is_manual_restricted_and_current_main_only() -> None:
    text = _text(".github/workflows/deepseek-vps-controlled-ops.yml")
    assert "DeepSeek VPS Controlled Operations" in text
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "runs-on: [self-hosted, linux, x64, boot-vps]" in text
    assert "options: [inspect, test, deploy]" in text
    assert "sudo /usr/local/sbin/status-boot-trading-bot" in text
    assert 'sudo /usr/local/sbin/deploy-boot-trading-bot "$target"' in text
    assert "checkout is not exact current origin/main" in text
    assert "secrets.DEEPSEEK_API_KEY" in text
    assert "https://api.deepseek.com/anthropic" in text
    assert "--permission-mode plan" in text
    assert "provider':'deepseek'" in text
    assert "root_shell':False" in text
    assert "arbitrary_sudo':False" in text
    assert "wallet_or_private_key_access':False" in text
    assert "arbitrary_deploy_sha':False" in text
    assert "deploy_current_main_via_restricted_wrapper_only':True" in text
    assert "vps/deepseek/latest.json" in text
    assert "AGENT: DEEPSEEK" in text
    assert "AI-Agent: DEEPSEEK" in text

    # Dangerous execution primitives must not be available to DeepSeek.
    for forbidden in ("sudo bash", "sudo sh", "sudo -i", "PRIVATE_KEY"):
        assert forbidden not in text

    # Verify the redaction behaviour without requiring one exact spelling of the
    # sensitive-word regex. Both the literal and split-string hardening forms are
    # acceptable as long as the same sensitive categories are redacted.
    has_literal_secret_terms = "mnemonic|seed phrase|password" in text
    has_split_secret_terms = (
        "'mne'+'monic'" in text
        and "'seed'+' phrase'" in text
        and "secret_terms" in text
        and "password" in text
    )
    assert has_literal_secret_terms or has_split_secret_terms
    assert "api[_ -]?key" in text
    assert "private[_ -]?key" in text
    assert "[REDACTED SENSITIVE LINE]" in text


def test_vps_deploy_has_no_user_selected_sha_or_branch() -> None:
    text = _text(".github/workflows/deepseek-vps-controlled-ops.yml")
    assert "target_sha:" not in text.split("on:", 1)[1].split("permissions:", 1)[0]
    assert "branch:" not in text.split("on:", 1)[1].split("permissions:", 1)[0]
    assert "git fetch --force origin main" in text
    assert 'current="$(git rev-parse HEAD' in text
    assert 'target="$(git rev-parse origin/main' in text
    assert '"$current" == "$target"' in text
