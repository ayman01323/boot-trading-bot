from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow_text(name: str = "deploy-vps.yml") -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_deploy_workflow_never_cancels_in_progress_vps_deploy():
    text = _workflow_text()
    assert "group: boot-vps-deploy" in text
    assert "cancel-in-progress: false" in text


def test_watcher_log_marker_is_observability_not_deployment_gate():
    text = _workflow_text()
    deployment_line = next(line for line in text.splitlines() if "deployment_ok=" in line)
    assert "watcher_ok" not in deployment_line
    assert "sha_ok" in deployment_line
    assert "service_ok" in deployment_line
    assert "deploy_outcome=='success'" in deployment_line


def test_communication_only_relays_do_not_use_boot_vps_runner():
    for name in ("universal-ai-bus-mailbox-relay.yml", "ai-mailbox-provider-relay.yml"):
        text = _workflow_text(name)
        assert "runs-on: [self-hosted, linux, x64, boot-vps]" not in text

    universal = _workflow_text("universal-ai-bus-mailbox-relay.yml")
    provider = _workflow_text("ai-mailbox-provider-relay.yml")
    assert "runs-on: ubuntu-latest" in universal
    assert "runs-on: [self-hosted, linux, x64, boot-google]" in provider
