from pathlib import Path

from learnerbot import ai_master_control as control


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_control_sanitises_bounded_deepseek_requests():
    value = control.sanitise(
        {
            "deepseek_github_action": "draft_fix",
            "deepseek_github_action_nonce": "7",
            "deepseek_github_task": " fix a proven bug \x00 ",
            "deepseek_vps_action": "deploy",
            "deepseek_vps_action_nonce": "4",
        }
    )
    assert value["deepseek_github_action"] == "draft_fix"
    assert value["deepseek_github_action_nonce"] == 7
    assert value["deepseek_github_task"] == "fix a proven bug"
    assert value["deepseek_vps_action"] == "deploy"
    assert value["deepseek_vps_action_nonce"] == 4


def test_invalid_deepseek_actions_are_downgraded_to_none():
    value = control.sanitise(
        {
            "deepseek_github_action": "merge",
            "deepseek_vps_action": "shell",
        }
    )
    assert value["deepseek_github_action"] == "none"
    assert value["deepseek_vps_action"] == "none"


def test_telegram_bridge_is_master_only_and_requires_deploy_confirmation():
    text = _text("learnerbot/telegram_deepseek_control_patch.py")
    assert "_menu._is_master(app, tid)" in text
    assert '"🔴 DeepSeek GitHub & VPS"' in text
    assert '"dsctl:gh:inspect"' in text
    assert '"dsctl:gh:test"' in text
    assert '"dsctl:gh:fix"' in text
    assert '"dsctl:vps:inspect"' in text
    assert '"dsctl:vps:test"' in text
    assert '"dsctl:vps:confirm"' in text
    assert '"dsctl:vps:deploy"' in text
    assert "request_deepseek_github_action" in text
    assert "request_deepseek_vps_action" in text
    assert "Confirm DeepSeek deploy CURRENT main" in text
    # Telegram only writes a sanitised request; it never executes shell/sudo itself.
    for dangerous in ("subprocess.", "os.system(", "sudo /", "ssh ", "DEEPSEEK_API_KEY"):
        assert dangerous not in text


def test_publisher_dispatches_only_new_deepseek_nonces():
    text = _text(".github/workflows/publish-ai-master-control.yml")
    assert "DEEPSEEK_GITHUB_REQUEST" in text
    assert "DEEPSEEK_VPS_REQUEST" in text
    assert "deepseek-github-controlled-ops.yml" in text
    assert "deepseek-vps-controlled-ops.yml" in text
    assert "int(new.get(key) or 0) > int(old.get(key) or 0)" in text
    assert "actions: write" in text


def test_deepseek_github_workflow_publishes_telegram_readable_result():
    text = _text(".github/workflows/deepseek-github-controlled-ops.yml")
    assert "github/deepseek/latest.json" in text
    assert "Record bounded DeepSeek GitHub result" in text
    assert "'draft_pr_only':True" in text
    assert "'self_merge':False" in text
    assert "'deploy_from_github_workflow':False" in text
    assert "gh pr create --draft" in text


def test_bridge_is_installed_after_five_agent_ui():
    text = _text("learnerbot/telegram_command_scope_patch.py")
    assert "telegram_five_agent_patch" in text
    assert "telegram_deepseek_control_patch" in text
    assert text.index("telegram_five_agent_patch") < text.index("telegram_deepseek_control_patch")
